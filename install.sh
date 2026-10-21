#!/usr/bin/env bash
# 一键安装：装到 Hermes / Claude Code / 任意目录。
#   bash install.sh                  # 自动检测（Hermes 和 Claude Code 都装）
#   bash install.sh --target claude  # 只装 Claude Code
#   bash install.sh --target hermes  # 只装 Hermes
#   bash install.sh --target ~/my-agents/skills
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
NAME="aliexpress-product-images"
TARGET="auto"
if [ "${1:-}" = "--target" ]; then TARGET="${2:?--target 需要一个路径或 hermes|claude}"; fi

install_to() {   # $1 = 放 skill 的父目录
  local parent="$1" dest="$1/$NAME"
  mkdir -p "$parent"
  rm -rf "$dest"
  cp -R "$HERE" "$dest"
  chmod +x "$dest/axn" "$dest"/scripts/*.py "$dest/install.sh" 2>/dev/null || true
  echo "  装好 -> $dest"
  echo "  用法 -> \"$dest/axn\" auto --src 新 --review"
}

case "$TARGET" in
  auto)
    CLAUDE_DIR="$HOME/.claude/skills"
    HERMES_DIR="${HERMES_HOME:-$HOME/.hermes}/skills/creative"
    did=0
    if [ -d "$CLAUDE_DIR" ] || command -v claude >/dev/null 2>&1; then
      echo "装到 Claude Code："; install_to "$CLAUDE_DIR"; did=1
    fi
    if [ -d "${HERMES_HOME:-$HOME/.hermes}" ] || command -v hermes >/dev/null 2>&1; then
      echo "装到 Hermes："; install_to "$HERMES_DIR"; did=1
    fi
    if [ "$did" = 0 ]; then
      echo "没检测到 Hermes / Claude Code，装到当前目录 skills/："
      install_to "$PWD/skills"
    fi
    ;;
  claude) install_to "$HOME/.claude/skills" ;;
  hermes) install_to "${HERMES_HOME:-$HOME/.hermes}/skills/creative" ;;
  *)      install_to "$(eval echo "$TARGET")" ;;
esac

# 顺手把 axn 放进 PATH（存在 ~/.local/bin 时）
if [ -d "$HOME/.local/bin" ]; then
  for c in "$HOME/.claude/skills/$NAME/axn" "${HERMES_HOME:-$HOME/.hermes}/skills/creative/$NAME/axn" "$PWD/skills/$NAME/axn"; do
    if [ -x "$c" ]; then
      ln -sf "$c" "$HOME/.local/bin/axn" && echo "已链接命令: axn -> $c"
      break
    fi
  done
fi

python3 - <<'PY' || echo "提醒：缺 Pillow，叠印产品印字前先 pip install Pillow"
import PIL; print("  Pillow", PIL.__version__, "OK")
PY
[ -z "${SENSENOVA_API_KEY:-}" ] && echo "提醒：还没设 key -> export SENSENOVA_API_KEY=sk-xxxx"
exit 0
