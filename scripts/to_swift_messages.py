"""把 sharegpt(conversations/from/value) 转成 ms-swift 标准 messages 格式。

用法：
    python to_swift_messages.py            # 默认读同目录上一级的 A_数据集，写到 ./swift_data
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT.parent / "A_数据集"
DST = ROOT / "swift_data"

SPLITS = {
    "train": "安卡希雅_训练_train.jsonl",
    "val": "安卡希雅_训练_val.jsonl",
    "test": "安卡希雅_训练_test.jsonl",
}

ROLE_MAP = {"system": "system", "user": "user", "assistant": "assistant"}


def convert(src: Path, dst: Path) -> tuple[int, int]:
    dst.parent.mkdir(parents=True, exist_ok=True)
    n_ok = n_bad = 0
    with src.open(encoding="utf-8") as fin, dst.open("w", encoding="utf-8") as fout:
        for line in fin:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            messages = []
            for turn in obj.get("conversations", []):
                role = ROLE_MAP.get(turn.get("from", "").strip())
                content = (turn.get("value") or "").strip()
                if role is None or not content:
                    continue
                if messages and messages[-1]["role"] == role:
                    messages[-1]["content"] += "\n" + content
                else:
                    messages.append({"role": role, "content": content})

            if len(messages) < 2 or messages[-1]["role"] != "assistant":
                n_bad += 1
                print(f"  [skip] 结尾不是 assistant 或轮次不足：{obj.get('source', '?')}")
                continue

            fout.write(json.dumps({"messages": messages}, ensure_ascii=False) + "\n")
            n_ok += 1
    return n_ok, n_bad


def main() -> int:
    total = 0
    for split, name in SPLITS.items():
        src = SRC / name
        if not src.exists():
            print(f"[warn] 缺文件：{src}")
            continue
        dst = DST / f"anka_{split}.jsonl"
        ok, bad = convert(src, dst)
        total += ok
        print(f"{split:5s} -> {dst.name}: {ok} 条（跳过 {bad}）")
    print(f"合计 {total} 条，输出目录：{DST}")
    return 0


if __name__ == "__main__":
    sys.exit(main())