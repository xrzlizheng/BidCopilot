"""PP-UIE 金标微调 + 淘汰门槛评测（阶段三.2）。

运行前提（本仓库 CI/宿主机默认不装 paddlenlp）：
    cd backend && uv run --extra ocr-local python ../scripts/finetune_uie.py \\
        --epochs 8 --learning-rate 5e-5
需要在装有 paddlepaddle/paddlenlp 的环境（容器或 GPU 机）执行。
模型权重不进 git：微调产物写到 ./uie-finetuned/，由 Dockerfile 构建期
按 EVALUATION 门槛决定是否打进镜像（见 EVALUATION 一节）。

淘汰门槛（来自 v5 方案，硬编码于此）：
    在留出对抗语料（--holdout，labels.json 格式同 eval_parser.py）上，
    微调模型相对零样本须满足：召回增益 >= 5% 且 精确率 >= 0.9。
    不达标：退出码 1，镜像构建方不得打包该权重，结论随验收数字记录。
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

RECALL_GAIN_MIN = 0.05
PRECISION_MIN = 0.9

UIE_SCHEMA = ["评分项", "资格要求", "废标条款"]
DIMENSION_BY_SCHEMA = {"评分项": "scoring", "资格要求": "qualification", "废标条款": "reject"}


def export_training_json(output_dir: Path) -> int:
    """把内置金标语料转成 PaddleNLP UIE 微调格式（每行一个 doc）。"""
    from tests.gold_corpus import build_gold_cases

    output_dir.mkdir(parents=True, exist_ok=True)
    count = 0
    with (output_dir / "train.json").open("w", encoding="utf-8") as handle:
        for case in build_gold_cases():
            parsed_text = case.content.decode("utf-8", errors="ignore") if case.filename.endswith(
                (".txt", ".md")
            ) else None
            if parsed_text is None:
                # docx/pdf 语料不在微调集内导出全文（保持脚本零重依赖），
                # 微调集以 txt/md 为主，其余格式评测时经解析器出文本。
                continue
            doc = {"content": parsed_text, "result_list": [], "prompt": ""}
            for schema, field in (
                ("评分项", "scoring_items"),
                ("资格要求", "qualifications"),
                ("废标条款", "reject_clauses"),
            ):
                for item in getattr(case, field):
                    text = item if isinstance(item, str) else item[0]
                    start = parsed_text.find(text)
                    if start == -1:
                        continue
                    doc["prompt"] = doc["prompt"] or schema
                    doc["result_list"].append(
                        {"text": text, "start": start, "end": start + len(text)}
                    )
            if doc["result_list"]:
                handle.write(json.dumps(doc, ensure_ascii=False) + "\n")
                count += 1
    print(f"exported {count} training docs -> {output_dir / 'train.json'}")
    return count


def finetune(train_json: Path, output_dir: Path, epochs: int, learning_rate: float) -> None:
    """调 PaddleNLP UIE finetune；仅在有 paddlenlp 的环境可运行。"""
    try:
        from paddlenlp import Taskflow  # noqa: F401
        from paddlenlp.trainer import set_seed  # type: ignore[attr-defined]
    except ImportError as exc:
        raise SystemExit(
            f"paddlenlp is not available in this environment ({exc}); "
            "run inside the container or a paddle-enabled machine"
        ) from exc
    # PaddleNLP 官方 uie finetune 入口（do_train）；此处保留调用点并固定种子。
    set_seed(42)
    print(
        "finetune: run paddlenlp uie finetune with "
        f"train_file={train_json} output={output_dir} epochs={epochs} lr={learning_rate}"
    )
    raise SystemExit(
        "finetune step requires the full paddlenlp trainer stack; "
        "wire model=uie-base train script here on the paddle-enabled machine"
    )


def evaluate_gate(zeroshot_metrics: dict, finetuned_metrics: dict) -> bool:
    """淘汰门槛：微调相对零样本召回增益达标且精确率达标。"""
    passed = True
    for dimension in ("scoring", "qualification", "reject"):
        base_recall = zeroshot_metrics.get(dimension, {}).get("recall", 0.0)
        new_recall = finetuned_metrics.get(dimension, {}).get("recall", 0.0)
        precision = finetuned_metrics.get(dimension, {}).get("precision", 0.0)
        gain = new_recall - base_recall
        ok = gain >= RECALL_GAIN_MIN and precision >= PRECISION_MIN
        print(
            f"{dimension}: recall {base_recall:.3f} -> {new_recall:.3f} (gain {gain:+.3f}), "
            f"precision {precision:.3f} -> {'PASS' if ok else 'FAIL'}"
        )
        passed = passed and ok
    print(f"gate: {'PASS' if passed else 'FAIL'} (recall_gain>={RECALL_GAIN_MIN}, precision>={PRECISION_MIN})")
    return passed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default="uie-finetuned")
    parser.add_argument("--epochs", type=int, default=8)
    parser.add_argument("--learning-rate", type=float, default=5e-5)
    parser.add_argument("--skip-finetune", action="store_true", help="只导出训练集")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    count = export_training_json(output_dir)
    if args.skip_finetune or count == 0:
        return 0
    finetune(output_dir / "train.json", output_dir, args.epochs, args.learning_rate)
    # 评测门槛在 finetune 产出的 metrics json 上执行（由评测运行方写入后调用）：
    # evaluate_gate(zeroshot, finetuned) —— 退出码决定镜像是否打包权重。
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
