"""下载本地 embedding 模型到项目目录."""

from pathlib import Path

from huggingface_hub import snapshot_download

from app.config import config


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def resolve_model_dir(path: str) -> Path:
    model_dir = Path(path).expanduser()
    if not model_dir.is_absolute():
        model_dir = PROJECT_ROOT / model_dir
    return model_dir.resolve()


def remove_appledouble_files(root: Path) -> None:
    """清理 macOS 在外置盘上可能生成的 AppleDouble 元数据文件."""
    for metadata_file in root.rglob("._*"):
        if metadata_file.is_file():
            metadata_file.unlink()


def main() -> None:
    model_dir = resolve_model_dir(config.local_embedding_model_path)
    model_dir.mkdir(parents=True, exist_ok=True)

    print(f"📥 下载 embedding 模型: {config.local_embedding_model_repo}")
    print(f"📁 本地目录: {model_dir}")

    snapshot_download(
        repo_id=config.local_embedding_model_repo,
        local_dir=str(model_dir),
        allow_patterns=[
            "*.json",
            "*.safetensors",
            "*.txt",
            "*.model",
            "*.py",
            "*.tiktoken",
        ],
    )
    remove_appledouble_files(model_dir)

    print("✅ embedding 模型下载完成")


if __name__ == "__main__":
    main()
