#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""一键包自检 —— 专治「脚本漏进包 / 漏 Shipped 到工作目录」这类静默失败。

为什么需要它
------------
v21 出过一个很贵的 bug：run_all.sh 第 7/7 步要跑 fast_eval.py，但
  · start.sh 的文件复制清单里没有它
  · run_all.sh 的解压清单 DATA_FILES 里也没有它
→ $BASE/fast_eval.py 从未存在。更糟的是那行是 `... | tail -40`，
  管道退出码取 tail 的 0，set -e 不触发，脚本一路跑到「全部完成 ✅」。
白跑一两小时，还以为成功了。

根本原因是一条隐含契约：**run_all.sh 里用绝对路径调用的每个脚本，
必须出现在 start.sh 的复制清单里，也必须出现在压缩包里。**
这个脚本就是把这条契约变成自动检查。

检查项
------
1. start.sh 的 REQUIRED 清单 ⊆ 包内容
2. run_all.sh 里所有 `$BASE/<name>.py|.sh` 引用 ⊆ REQUIRED 清单
   （这条最关键 —— 新增脚本忘了同步清单会当场被抓）
3. run_all.sh 的 DATA_FILES ⊆ 包内容
4. 所有 .sh / .py 必须是 LF（CRLF 会让 Linux bash 报 `$'\r'` 秒退）
5. 训练集行数 == 断言值
6. 所有 .sh 语法可用 `bash -n` 通过（有 bash 时）

用法
----
    python pack_check.py                       # 自动找最新的包
    python pack_check.py <tar.gz 路径>
    python pack_check.py --expect-train 2016   # 指定行数断言

退出码 0 = 全通过；1 = 有错（CI / 打包后必跑）。
"""
from __future__ import annotations

import io
import os
import re
import subprocess
import sys
import tarfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

HERE = Path(__file__).resolve().parent.parent.parent / "E_训练配置"   # → finetune/


def find_latest_tgz() -> Path:
    cands = sorted(HERE.glob("anka_云上一键包_v*.tar.gz"),
                   key=lambda p: int(re.search(r"_v(\d+)\.tar\.gz$", p.name).group(1)))
    if not cands:
        raise SystemExit(f"没找到包：{HERE}/anka_云上一键包_v*.tar.gz")
    return cands[-1]


def read_text(tf: tarfile.TarFile, name: str) -> str:
    return tf.extractfile(name).read().decode("utf-8", "replace")


def parse_config(runall: str) -> dict:
    """从 run_all.sh 的「集中配置区」里抽出 `NAME=value` 形式的赋值。

    只认配置区那一小段（★ 集中配置区 ★ 之前/之后数行），避免把脚本正文里
    的同名局部变量也算进来。值统一按字符串返回，纯数字再转 int。
    """
    if "★ 集中配置区" not in runall:
        return {}
    # 取配置区到 banner() 定义之间的小片段
    seg = runall.split("★ 集中配置区", 1)[1]
    seg = seg.split("banner()", 1)[0]
    cfg: dict = {}
    for m in re.finditer(r'^\s*([A-Z][A-Z0-9_]*)=("[^"]*"|\'[^\']*\'|[^\s#]+)',
                         seg, re.M):
        name, raw = m.group(1), m.group(2)
        val = raw.strip().strip('"').strip("'")
        if val.isdigit():
            cfg[name] = int(val)
        else:
            cfg[name] = val
    return cfg


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    tgz = Path(args[0]) if args else find_latest_tgz()
    expect = None
    if "--expect-train" in sys.argv:
        expect = int(sys.argv[sys.argv.index("--expect-train") + 1])

    print("=" * 68)
    print(f"一键包自检：{tgz.name}")
    print("=" * 68)

    errors: list[str] = []
    warns: list[str] = []

    with tarfile.open(tgz) as tf:
        members = {m.name for m in tf.getmembers() if m.isfile()}
        print(f"包内 {len(members)} 个文件\n")

        if "start.sh" not in members:
            raise SystemExit("包里没有 start.sh，先检查打包命令")

        start = read_text(tf, "start.sh")
        runall = read_text(tf, "run_all.sh")

        # ── 1. start.sh 的 REQUIRED 清单 ⊆ 包 ────────────────────────────
        print("── 1. start.sh 复制清单 vs 包内容 ──")
        m = re.search(r'^\s*REQUIRED\s*=\s*"([^"]+)"', start, re.M)
        if not m:
            errors.append("start.sh 里找不到 REQUIRED 变量，自检无从下手")
            required: list[str] = []
        else:
            required = m.group(1).split()
        for f in required:
            ok = f in members
            print(f"  {'✓' if ok else '✗'} {f}")
            if not ok:
                errors.append(f"start.sh 要复制 {f}，但包里没有")
        print()

        # ── 2. run_all.sh 引用的脚本 ⊆ REQUIRED ──────────────────────────
        #     这条是 v21 那个 bug 的直接检测器
        print("── 2. run_all.sh 调用的脚本是否已进复制清单 ──")
        refs = sorted(set(re.findall(r'\$BASE/([A-Za-z0-9_\-]+\.(?:py|sh))', runall)))
        if not refs:
            warns.append("run_all.sh 里没找到 $BASE/xxx.py 形式的引用，检查正则是否过时")
        for f in refs:
            ok = f in required
            print(f"  {'✓' if ok else '✗'} {f:28s} {'在清单里' if ok else '**没在 REQUIRED 里 → 云端会找不到它**'}")
            if not ok:
                errors.append(f"run_all.sh 调用了 $BASE/{f}，但 start.sh 不会复制它")
        print()

        # ── 3. DATA_FILES ⊆ 包 ────────────────────────────────────────────
        print("── 3. run_all.sh 解压清单 DATA_FILES vs 包内容 ──")
        dm = re.search(r'DATA_FILES="([^"]+)"', runall, re.S)
        if dm:
            # bash 的续行符 `\` 会被 split 误当成文件名，过滤掉
            data_files = [x for x in dm.group(1).split()
                          if x.strip() and x.strip() != "\\"]
            miss = [f for f in data_files if f not in members]
            print(f"  DATA_FILES 共 {len(data_files)} 项，缺 {len(miss)} 项")
            for f in miss:
                print(f"    ✗ {f}")
                errors.append(f"DATA_FILES 里的 {f} 不在包里（解压会失败）")
        else:
            warns.append("run_all.sh 里找不到 DATA_FILES 清单")
        # 压测集是硬要求
        for f in ("压测24条_身份_全卡.jsonl", "压测24条_身份_无卡.jsonl",
                  "压测24条_身份_记忆块.jsonl", "压测15条_情感_全卡.jsonl",
                  "压测15条_情感_无卡.jsonl"):
            if f not in members:
                errors.append(f"缺压测集 {f}")
        print()

        # ── 3b. run_all.sh 找包时不得写死版本号 ────────────────────────────
        #       v23 首跑踩的坑：脚本里还写着 `anka_云上一键包_v22.tar.gz`，
        #       v23 匹配不上 → TGZ 空 → exit 1，而 `ls 2>/dev/null` 把线索吞了，
        #       日志只剩「!!!! 第 67 行附近失败」，极难查。
        #       判据：run_all.sh 里出现的**任何** anka_云上一键包_vNN.tar.gz 字面
        #       （注释行、纯提示 echo 除外）都算写死 —— 包括恰好等于本包名的那种，
        #       因为那正是「这包能用、下个包必炸」的状态（v22 就是这样）。
        #
        #       排除「纯提示文案」的理由：`echo "...把 vNN.tar.gz 拖进去..."` 是给人看的，
        #       不参与找包；但它同样会随版本号过期变成误导，所以脚本里改用 `v*` 通配文案，
        #       检查器也只在**非 echo 行**里判写死。
        print("── 3b. run_all.sh 找包逻辑是否写死版本 ──")
        hard: list[str] = []
        for ln_no, line in enumerate(runall.splitlines(), 1):
            st = line.strip()
            if st.startswith("#"):
                continue                      # 注释里提到旧版本是正常的
            if st.startswith("echo") or st.startswith("printf"):
                continue                      # 纯提示文案，不参与找包
            for m2 in re.finditer(r'anka_云上一键包_v\d+\.tar\.gz', line):
                hard.append(f"第 {ln_no} 行写死了 {m2.group(0)}")
        # 通配写法（v*.tar.gz）是正确做法
        has_glob = bool(re.search(r'anka_云上一键包_v\*\.tar\.gz', runall))
        if has_glob:
            print("  ✓ 使用通配 anka_云上一键包_v*.tar.gz（新增版本无需改脚本）")
        if hard:
            for h in hard:
                print(f"  ✗ {h}")
                errors.append(
                    f"run_all.sh 写死了包名（{hard[0]}）—— 换版本号后云端会找不到包直接退出；"
                    f"改用通配 anka_云上一键包_v*.tar.gz")
        elif not has_glob:
            print("  – 既没写死也没用通配，确认一下找包逻辑")
        else:
            print("  ✓ 未写死任何具体版本")
        print()

        # ── 4. LF 检查 ────────────────────────────────────────────────────
        print("── 4. 行尾（必须为 LF）──")
        for m_ in tf.getmembers():
            if not m_.isfile() or not m_.name.endswith((".sh", ".py")):
                continue
            raw = tf.extractfile(m_).read()
            crlf = raw.count(b"\r\n")
            print(f"  {'✓' if crlf == 0 else '✗'} {m_.name:28s} CRLF={crlf}")
            if crlf:
                errors.append(f"{m_.name} 含 {crlf} 处 CRLF —— Linux bash 会报 $'\\r' 秒退")
        print()

        # ── 5. 训练集行数 ────────────────────────────────────────────────
        print("── 5. 数据集行数 vs 断言 ──")
        n_train = len([l for l in io.TextIOWrapper(
            tf.extractfile("anka_train.jsonl"), encoding="utf-8") if l.strip()]) \
            if "anka_train.jsonl" in members else -1
        # v24：run_all.sh 改用「集中配置区」变量 TRAIN_ROWS/VAL_ROWS/TEST_ROWS，
        #      旧的 N1 正则已失效。优先从配置区取，再退回兼容老写法。
        cfg = parse_config(runall)
        if expect is None:
            if "TRAIN_ROWS" in cfg:
                expect = cfg["TRAIN_ROWS"]
            else:
                em = re.search(r'"\$N1" != "(\d+)"', runall) or \
                     re.search(r'N1\s*=\s*"(\d+)"', runall)
                expect = int(em.group(1)) if em else None
        print(f"  anka_train.jsonl = {n_train} 条")
        if expect:
            src = "配置区 TRAIN_ROWS" if "TRAIN_ROWS" in cfg else "run_all.sh 断言"
            print(f"  {src} = {expect}")
            if n_train != expect:
                errors.append(f"train 行数 {n_train} != 断言 {expect}，云端会直接停")
        exp_val = cfg.get("VAL_ROWS", 31)
        exp_test = cfg.get("TEST_ROWS", 31)
        for f, e in (("anka_val.jsonl", exp_val), ("anka_test.jsonl", exp_test)):
            if f not in members:
                continue
            n = len([l for l in io.TextIOWrapper(
                tf.extractfile(f), encoding="utf-8") if l.strip()])
            print(f"  {f:20s} = {n}（期望 {e}）")
            if n != e:
                errors.append(f"{f} 是 {n} 条，与配置区 {e} 不符")
        print()

        # ── 5b. 集中配置区自洽性 ──────────────────────────────────────────
        #       v24 新增配置区后，最容易出的错有两类：
        #         (a) 改了 BS/ACCUM 但没同步笔记/文档里的显存账（无法自动判，只提示）
        #         (b) 配置区缺关键变量（正文引用了 $BS 但配置区没定义）
        #       这里检查：正文引用的配置变量是否都在配置区定义过。
        print("── 5b. 集中配置区自洽性 ──")
        if not cfg and "★ 集中配置区" not in runall:
            warns.append("run_all.sh 里没有集中配置区（v24 前的老包，属正常）")
            print("  – 未发现集中配置区（老包）")
        else:
            cfg_vars = {"TRAIN_ROWS", "VAL_ROWS", "TEST_ROWS", "BS", "ACCUM",
                        "MAX_LEN", "EPOCHS", "LR", "WARMUP_RATIO", "LORA_RANK",
                        "LORA_ALPHA", "LORA_DROPOUT", "GRAD_CKPT", "PKG_GLOB"}
            # 正文（配置区之后）里以 $VAR 形式引用的量
            body = runall.split("★ 集中配置区", 1)[-1]
            used = set(re.findall(r'\$([A-Z][A-Z0-9_]+)', body))
            # 只关心我们定义的这批约定名，其余（BASE/VENV/...）由脚本早期定义
            missing = sorted(v for v in cfg_vars if v in used and v not in cfg)
            undefined = [v for v in used if v in cfg_vars and v not in cfg]
            for v in sorted(cfg_vars & set(cfg)):
                print(f"  · {v}={cfg[v]}")
            if missing:
                for v in missing:
                    print(f"  ✗ ${v} 在正文被引用，但配置区没有定义")
                    errors.append(f"配置区缺 {v}（正文引用了它）")
            else:
                print("  ✓ 正文引用的配置变量都已在配置区定义")
            print()

        # ── 5c. 配置区「真跑一遍」（抓 bash -n 抓不到的运行时错误）──────
        #   2026-10-04 事故：`STEPS_PER_EPOCH=$((...)))# 注释` —— `))` 与 `#` 之间没空格，
        #   bash 把 `#` 当作紧邻 `)` 的词的一部分（不是注释起始），整段注释被当成命令执行：
        #       run_all.sh: line 76: 优化器步数/epoch（=: No such file or directory
        #   而 **`bash -n` 完全通过**（语法合法，纯运行时错误）→ 云端秒退，排查两轮才发现。
        #   所以这里必须把配置区**真的执行一遍**，验证：① 不报错 ② 派生量算得对。
        print("── 5c. 配置区实跑（抓运行时错误）──")
        seg = runall.split("★ 集中配置区", 1)[-1].split("banner()", 1)[0] if cfg else ""
        # 先探测本机 bash 能不能起来 —— 起不来就跳过（环境问题，不该报成包的错）。
        # 否则会把「本机无 bash」误判成「配置区有 bug」，自检失去可信度。
        def _bash_alive() -> bool:
            try:
                pr = subprocess.run(["bash", "-c", "echo ok"], capture_output=True,
                                    timeout=15)
                return pr.returncode == 0 and b"ok" in (pr.stdout or b"")
            except Exception:
                return False

        if not seg:
            print("  – 无配置区，跳过")
        elif not _bash_alive():
            warns.append("本机 bash 起不来，跳过配置区实跑（请在 git bash / Linux 补验一次）")
            print("  – 本机无可用 bash，跳过（**务必在能跑 bash 的环境补验**）")
        else:
            tmp2 = Path(os.environ.get("TEMP", ".")) / "_cfgchk"
            tmp2.mkdir(parents=True, exist_ok=True)
            sh = tmp2 / "cfg.sh"
            # 取出配置区里的赋值 + 派生量计算行。
            # 过滤规则要覆盖：① 以 # 开头的注释 ② 以 ★ 开头的装饰行（配置区标题）
            #   ③ echo/printf 输出行（只产生输出，不影响校验，留着会干扰 STEPS 提取）
            def _keep(l: str) -> bool:
                s = l.strip()
                if not s:
                    return False
                if s.startswith("#") or s.startswith("★"):
                    return False
                if s.startswith("echo") or s.startswith("printf"):
                    return False
                return True
            body = "\n".join(l for l in seg.splitlines() if _keep(l))
            script = ("set -e\n" + body +
                      '\necho "STEPS=$STEPS_PER_EPOCH TOTAL=$TOTAL_STEPS CKPT=$CKPT_LIST"\n')
            sh.write_text(script, encoding="utf-8", newline="\n")
            try:
                r = subprocess.run(["bash", str(sh)], capture_output=True, timeout=30)
                out = (r.stdout or b"").decode("utf-8", "replace").strip()
                err = (r.stderr or b"").decode("utf-8", "replace").strip()
                if r.returncode != 0:
                    print(f"  ✗ 配置区执行失败（退出码 {r.returncode}）")
                    if err:
                        print(f"    {err.splitlines()[-1]}")
                    errors.append(f"配置区实跑失败：{err.splitlines()[-1] if err else '未知错误'}")
                else:
                    # 校验派生量：STEPS == ceil(TRAIN_ROWS/(BS*ACCUM))
                    import math
                    tr, bs_, ac = cfg.get("TRAIN_ROWS"), cfg.get("BS"), cfg.get("ACCUM")
                    ep = cfg.get("EPOCHS", 4)
                    if all(isinstance(x, int) for x in (tr, bs_, ac)):
                        exp = math.ceil(tr / (bs_ * ac))
                        m = re.search(r"STEPS=(\d+) TOTAL=(\d+)", out)
                        if m:
                            got, tot = int(m.group(1)), int(m.group(2))
                            ok = got == exp and tot == exp * ep
                            print(f"  {'✓' if ok else '✗'} 优化器步数 {got}/epoch（期望 {exp}）"
                                  f"  总 {tot}（期望 {exp*ep}）")
                            if not ok:
                                errors.append(
                                    f"配置区派生量算错：{got} 应 = ceil({tr}/({bs_}×{ac})) = {exp}")
                        else:
                            print(f"  ⚠ 配置区跑通但没拿到 STEPS（输出：{out[:80]}）")
                            warns.append("配置区实跑输出里没找到 STEPS=，检查 5c 的注入行")
                    else:
                        print("  – 数值型配置不全，跳过派生量校验（仅验不报错）")
            except Exception as e:
                warns.append(f"配置区实跑未能执行（{e}），请在 git bash 手动验一次")
                print(f"  – 无法执行（{e}）")
        print()

        # ── 6. bash 语法 ─────────────────────────────────────────────────
        print("── 6. bash -n 语法检查 ──")
        try:
            probe = subprocess.run(["bash", "-c", "echo ok"], capture_output=True,
                                   timeout=15)
            bash_ok = (probe.returncode == 0
                       and b"ok" in (probe.stdout or b""))
        except Exception:
            bash_ok = False
        if not bash_ok:
            # 本机 bash 起不来（Windows 沙箱可能把 wsl.exe 列入黑名单）。
            # 这是环境问题，不是包的问题 —— 标为跳过，别误报成错误。
            warns.append("本机没有可用的 bash，跳过语法检查；"
                         "请在 git bash / Linux 里手动 `bash -n <脚本>` 补检一次")
            print("  – 本机无可用 bash，跳过")
        tmp = Path(os.environ.get("TEMP", ".")) / "_packchk"
        for m_ in tf.getmembers():
            if not bash_ok or not m_.isfile() or not m_.name.endswith(".sh"):
                continue
            tmp.mkdir(parents=True, exist_ok=True)
            p = tmp / m_.name
            p.write_bytes(tf.extractfile(m_).read())
            try:
                r = subprocess.run(["bash", "-n", str(p)], capture_output=True,
                                   timeout=20)
                out = (r.stderr or b"").decode("utf-8", "replace")
                rc = r.returncode
            except Exception as e:
                out, rc = str(e), 1
            print(f"  {'✓' if rc == 0 else '✗'} {m_.name}")
            if rc != 0:
                errors.append(f"{m_.name} 语法错误：{out.strip()[:160]}")
        if bash_ok and not [e for e in errors if "语法错误" in e]:
            print("  （全部通过）")

    # ── 汇总 ────────────────────────────────────────────────────────────
    print("\n" + "=" * 68)
    for w in warns:
        print(f"  ⚠ {w}")
    if errors:
        print(f"  共 {len(errors)} 项错误：")
        for e in errors:
            print(f"    ✗ {e}")
        print("自检未通过 —— 修完再上传，别让云端替你发现。")
        print("=" * 68)
        return 1
    print("  全部通过 ✓  可以上传了。")
    print("=" * 68)
    return 0


if __name__ == "__main__":
    sys.exit(main())
