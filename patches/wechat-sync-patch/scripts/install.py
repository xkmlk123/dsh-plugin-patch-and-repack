#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把打过补丁的 host.js 装进 dsh-wechat-plugin。

用法：
    python scripts/install.py            # 只检查，不写（dry-run）
    python scripts/install.py --apply    # 真正写入
    python scripts/install.py --check    # 只验证当前是否已装好
    python scripts/install.py --rollback # 还原成插件原版

设计原则：
1. 先找包，再验版本。版本不对直接停——补丁是照着 0.46.0 的源码位置做的。
2. 写之前一定备份，备份文件名带版本号。
3. 写完立刻回读校验：字节数 + 7 个标记的命中数。只看"文件已复制"不算数。
4. 不碰 state.json、不碰绑定信息——那些属于每台机器自己的账号。
"""

import argparse
import hashlib
import io
import json
import os
import shutil
import sys

PATCHED = "host.js"
ORIGINAL = "host.js.original-0.46.0"
TARGET_VERSION = "0.46.0"

# 补丁特征：标记名 -> 期望出现次数。少了任何一条都说明装错了文件。
MARKERS = {
    "desktopFirstThenWechat": 3,
    "onSessionEvent": 2,
    "bufferUnboundReply": 2,
    "wechatAndDesktop": 3,
    "questionCalls": 6,
    "takeQuestionCall": 2,
    "rememberQuestionCall": 2,
}

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FILES = os.path.join(ROOT, "files")


def dsh_home():
    home = os.environ.get("DSH_HOME")
    if home:
        return home
    return os.path.join(os.path.expanduser("~"), ".dsh")


def find_plugins():
    """在所有 profile 下找 dsh-wechat-plugin，返回 [(profile名, 插件目录)]。"""
    found = []
    profiles = os.path.join(dsh_home(), "profiles")
    if not os.path.isdir(profiles):
        return found
    for name in sorted(os.listdir(profiles)):
        cand = os.path.join(profiles, name, "node_modules", "dsh-wechat-plugin")
        if os.path.isdir(cand):
            found.append((name, cand))
    return found


def read_text(path):
    with io.open(path, "r", encoding="utf-8", errors="surrogateescape") as fh:
        return fh.read()


def write_text(path, text):
    with io.open(path, "w", encoding="utf-8", errors="surrogateescape", newline="") as fh:
        fh.write(text)


def sha256(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def count_markers(text):
    return {name: text.count(name) for name in MARKERS}


def check_markers(counts):
    """返回缺失的标记列表。空列表 = 全部达标。"""
    bad = []
    for name, want in MARKERS.items():
        got = counts.get(name, 0)
        if got != want:
            bad.append("%s 期望 %d 次，实际 %d 次" % (name, want, got))
    return bad


def plugin_version(plugin_dir):
    pkg = os.path.join(plugin_dir, "package.json")
    if not os.path.isfile(pkg):
        return None
    try:
        with io.open(pkg, "r", encoding="utf-8") as fh:
            return json.load(fh).get("version")
    except Exception:
        return None


def host_path(plugin_dir):
    return os.path.join(plugin_dir, "lib", "host.js")


def report_targets(plugins):
    if not plugins:
        print("找不到 dsh-wechat-plugin。")
        print("先装插件再回来：在 DSH 里装 dsh-wechat-plugin，或手动 pnpm add dsh-wechat-plugin。")
        return False
    print("找到 %d 个 dsh-wechat-plugin：" % len(plugins))
    for name, path in plugins:
        ver = plugin_version(path) or "未知"
        hp = host_path(path)
        exists = os.path.isfile(hp)
        size = os.path.getsize(hp) if exists else 0
        flag = "" if ver == TARGET_VERSION else "  <-- 版本不是 %s，补丁可能对不上" % TARGET_VERSION
        print("  profile=%-10s version=%-8s host.js=%-8s %s%s"
              % (name, ver, size if exists else "缺失", hp, flag))
    return True


def pick(plugins, wanted):
    if wanted:
        for name, path in plugins:
            if name == wanted:
                return name, path
        print("没有叫 %s 的 profile。可选：%s" % (wanted, ", ".join(n for n, _ in plugins)))
        return None, None
    if len(plugins) == 1:
        return plugins[0]
    # 多个 profile 时优先 desktop（桌面应用用的那个）
    for name, path in plugins:
        if name == "desktop":
            return name, path
    print("有多个 profile，请用 --profile 指定：%s" % ", ".join(n for n, _ in plugins))
    return None, None


def do_check(plugin_dir):
    hp = host_path(plugin_dir)
    if not os.path.isfile(hp):
        print("FAIL: %s 不存在" % hp)
        return 1
    counts = count_markers(read_text(hp))
    bad = check_markers(counts)
    if bad:
        print("FAIL: 补丁没装好")
        for line in bad:
            print("  " + line)
        return 1
    print("OK: 补丁已就位，%d 字节，sha256=%s" % (os.path.getsize(hp), sha256(hp)[:16]))
    for name, want in MARKERS.items():
        print("  %-24s %d/%d" % (name, counts[name], want))
    return 0


def do_apply(plugin_dir):
    ver = plugin_version(plugin_dir)
    if ver != TARGET_VERSION:
        print("FAIL: 插件版本是 %s，补丁只对 %s 有效。" % (ver, TARGET_VERSION))
        print("要么先把插件降到 %s，要么自己按 references 里的方法重新打补丁。" % TARGET_VERSION)
        return 1

    src = os.path.join(FILES, PATCHED)
    if not os.path.isfile(src):
        print("FAIL: 找不到补丁文件 %s" % src)
        return 1

    hp = host_path(plugin_dir)
    os.makedirs(os.path.dirname(hp), exist_ok=True)

    # 备份：已有文件且不是补丁版本，才备份
    if os.path.isfile(hp):
        current = read_text(hp)
        if not check_markers(count_markers(current)):
            print("已经装过了，跳过写入。")
            return do_check(plugin_dir)
        bak = hp + ".before-patch-" + (ver or "unknown")
        if not os.path.isfile(bak):
            shutil.copy2(hp, bak)
            print("已备份原文件 -> %s" % bak)
        else:
            print("备份已存在，保留不动 -> %s" % bak)

    shutil.copy2(src, hp)
    print("已写入 %s" % hp)
    return do_check(plugin_dir)


def do_rollback(plugin_dir):
    hp = host_path(plugin_dir)
    ver = plugin_version(plugin_dir) or "unknown"
    candidates = [
        hp + ".before-patch-" + ver,
        os.path.join(FILES, ORIGINAL),
    ]
    for bak in candidates:
        if os.path.isfile(bak):
            shutil.copy2(bak, hp)
            print("已还原 -> %s（来源 %s）" % (hp, bak))
            return 0
    print("FAIL: 找不到可还原的原版文件，试过：")
    for bak in candidates:
        print("  " + bak)
    return 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真正写入")
    ap.add_argument("--check", action="store_true", help="只验证")
    ap.add_argument("--rollback", action="store_true", help="还原原版")
    ap.add_argument("--profile", default=None, help="指定 profile 名（默认 desktop）")
    args = ap.parse_args()

    plugins = find_plugins()
    if not report_targets(plugins):
        return 1

    name, plugin_dir = pick(plugins, args.profile)
    if plugin_dir is None:
        return 1
    print("目标 profile=%s" % name)
    print("")

    if args.rollback:
        return do_rollback(plugin_dir)
    if args.check:
        return do_check(plugin_dir)
    if args.apply:
        return do_apply(plugin_dir)

    # 默认 dry-run
    hp = host_path(plugin_dir)
    print("DRY-RUN：准备把 files/%s 写入 %s" % (PATCHED, hp))
    print("  补丁 %d 字节  sha256=%s" % (os.path.getsize(os.path.join(FILES, PATCHED)),
                                      sha256(os.path.join(FILES, PATCHED))[:16]))
    if os.path.isfile(hp):
        counts = count_markers(read_text(hp))
        bad = check_markers(counts)
        print("  当前文件 %d 字节，补丁标记：%s" % (os.path.getsize(hp), "已装" if not bad else "未装"))
    print("")
    print("确认无误后加 --apply 真正写入。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
