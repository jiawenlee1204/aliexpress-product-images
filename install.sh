#!/usr/bin/env bash
# 安装：复制到 agent 的 skills 目录。
#   bash install.sh                  自动检测 Claude Code / Hermes，都装
#   bash install.sh --target claude  只装 Claude Code（~/.claude/skills/）
#   bash install.sh --target hermes  只装 Hermes（$HERMES_HOME/skills/creative/）
#   bash install.sh --target <目录>  装到指定目录
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
NAME="aliexpress-product-images"
TARGET="auto"
if [ "${1:-}" = "--target" ]; then TARGET="${2:?--target 需要一个路径或 hermes|claude}"; fi

install_to() {
  local parent="$1" dest="$1/$NAME"
  mkdir -p "$parent"
  rm -rf "$dest"
  cp -R "$HERE" "$dest"
  chmod +x "$dest"/scripts/*.py "$dest/install.sh" 2>/dev/null || true
  echo "  装到 $dest"
  echo "  试跑 python3 \"$dest/scripts/axn.py\" --help"
}

case "$TARGET" in
  auto)
    did=0
    if [ -d "$HOME/.claude/skills" ] || command -v claude >/dev/null 2>&1; then
      echo "Claude Code:"; install_to "$HOME/.claude/skills"; did=1
    fi
    if [ -d "${HERMES_HOME:-$HOME/.hermes}" ] || command -v hermes >/dev/null 2>&1; then
      echo "Hermes:"; install_to "${HERMES_HOME:-$HOME/.hermes}/skills/creative"; did=1
    fi
    if [ "$did" = 0 ]; then
      echo "没检测到 agent，装到当前目录 skills/："
      install_to "$PWD/skills"
    fi
    ;;
  claude) install_to "$HOME/.claude/skills" ;;
  hermes) install_to "${HERMES_HOME:-$HOME/.hermes}/skills/creative" ;;
  *)      install_to "$(eval echo "$TARGET")" ;;
esac

python3 - <<'PY' 2>/dev/null || echo "  注意：没装 Pillow，叠印印字前需要 pip install Pillow"
import PIL; print("  Pillow", PIL.__version__)
PY

if [ -z "${AXN_API_KEY:-}" ]; then
  echo
  echo "还没设密钥和模型，运行时需要（必须是支持图生图的模型）："
  echo "  export AXN_API_BASE=https://你的端点/v1"
  echo "  export AXN_API_KEY=sk-..."
  echo "  export AXN_IMAGE_MODEL=你的图生图模型"
  echo "  export AXN_VLM_MODEL=你的看图模型     # 只给 spec/auto 用，可选"
fi
exit 0
