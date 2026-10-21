#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""速卖通商品图批量出图：图生图（POST {base}/images/edits）+ 产品印字叠印。

配置（环境变量，或当前/上层目录的 .axn.json）：
  AXN_API_BASE     OpenAI 兼容端点，默认 https://token.sensenova.cn/v1
  AXN_API_KEY      API Key（.axn.json 里可用 api_key_env 指定读哪个环境变量）
  AXN_IMAGE_MODEL  生图模型，必须是支持图生图/图像编辑的模型（接收 images 输入）
  AXN_VLM_MODEL    看图模型，仅 spec/auto 起草提示词时需要；不配就手写 specs.json
  AXN_SIZE         输出尺寸，默认 2048x2048
.axn.json 键名：api_base / image_model / vlm_model / size / api_key_env

子命令：
  scan   <来货目录>                     列 SKU 与 Excel 机位要求
  spec   --src <目录>                   看图模型起草六段提示词到 _work/specs.json
  render --specs <json> --src <目录>    按规格出候选（--dry-run 只看计划）
  auto   --src <目录> [--review]        起草（缺规格时）→ 出图 → 复核 → 定稿
  pick   --sku <SKU> --cand <A|B>       候选定稿到 output/
  overlay --base <图> --out <图> --text "COMFORT|INSOLE"   叠印产品印字
  clean                                 把 output/ 里的过程目录挪进 _work/

项目目录约定：
  output/            成品 <SKU>.jpg
  _work/candidates/  候选 <SKU>_<候选>.jpg
  _work/replaced/    被替换的旧成品
  _work/specs.json   每个 SKU 的提示词规格

specs.json 结构：{"skus": {"SKU": {"A": {"identity": "...", "camera": "...", "adapt": "...",
"allow": "...", "forbid": "...", "tail": "..."}}}}
可选键：refs（额外参考图，相对该 SKU 目录）、overlay（出图后自动叠印的参数）。
"""
import argparse, base64, json, math, os, re, shutil, sys, time, urllib.error, urllib.request
import xml.etree.ElementTree as ET
import zipfile

try:                                    # 只有叠印产品印字需要 Pillow
    from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont
    HAVE_PIL = True
except ImportError:
    HAVE_PIL = False

# ── 网络：绕过 macOS 指向未启动 127.0.0.1:7890 的系统代理 ──
urllib.request.install_opener(urllib.request.build_opener(urllib.request.ProxyHandler({})))

# ── 配置：环境变量优先，其次当前目录（或上层）的 .axn.json ──
# 必须是「图生图 / 图像编辑」模型：接收 images 输入、按提示词改机位。纯文生图模型用不了。
CONFIG_KEYS = ("api_base", "image_model", "vlm_model", "size", "api_key_env")


def load_config():
    cfg = {}
    d = os.getcwd()
    for _ in range(4):
        p = os.path.join(d, ".axn.json")
        if os.path.exists(p):
            try:
                cfg = json.load(open(p, encoding="utf-8"))
            except Exception as e:
                sys.exit("%s 解析失败：%s" % (p, e))
            break
        d = os.path.dirname(d)
    env = {
        "api_base": os.environ.get("AXN_API_BASE") or os.environ.get("SENSENOVA_BASE_URL"),
        "image_model": os.environ.get("AXN_IMAGE_MODEL") or os.environ.get("SN_IMAGE_MODEL"),
        "vlm_model": os.environ.get("AXN_VLM_MODEL") or os.environ.get("SN_VLM_MODEL"),
        "size": os.environ.get("AXN_SIZE") or os.environ.get("SN_SIZE"),
    }
    for k, v in env.items():
        if v:
            cfg[k] = v
    key_env = cfg.get("api_key_env") or "AXN_API_KEY"
    cfg["api_key"] = (os.environ.get("AXN_API_KEY") or os.environ.get("SENSENOVA_API_KEY")
                      or os.environ.get(key_env, ""))
    cfg.setdefault("api_base", "https://token.sensenova.cn/v1")
    cfg.setdefault("image_model", "sensenova-u1.5-lite")
    cfg.setdefault("size", "2048x2048")
    return cfg


CFG = load_config()
BASE_URL = CFG["api_base"].rstrip("/")
URL = BASE_URL + "/images/edits"
MODEL = CFG["image_model"]
SIZE = CFG["size"]
VLM = CFG.get("vlm_model") or ""
API_KEY = CFG["api_key"]
ATTEMPTS = 3
TIMEOUT = 300
STAGGER = 6          # 串行 + 错峰，接口单并发，并发直接 429

NEED_I2I = ("当前模型可能不支持图生图（需要能接受 images 输入的图像编辑模型）。"
            "用 AXN_IMAGE_MODEL 换一个，或检查 AXN_API_BASE 是否正确：%s" % BASE_URL)

HEAD = """【第一条铁律 · 产品必须与图1完全一致】
产品是：{identity}。
形状、结构、比例、尺寸关系、颜色（色相与明度）、材质与表面工艺、logo/刻字/花纹、每一个部件及其数量，都必须与图1完全一致。禁止变形、拉伸、压扁、增删部件、改变数量、更换颜色、简化细节或替换成相似产品。

【关于图2的严格禁令】
图2只能用来参考：机位角度与透视、背景的材质与色调、光线方向与强弱、道具与产品的摆放关系、留白与构图比例。
图2里画的是另一款产品，图2里的品牌名、logo、中英文文字、价格与尺寸标注、水印、拼贴小图，一律不许进入成品，也不许把图2的产品画进画面。

【场景与适配】
{adapt}

【任务 · 只改变拍摄视角与场景，不改变产品】
{camera}

【电商主图标准】
1:1 正方形构图，产品是视觉中心，主体占画面约 70%~80%，四周留出干净的呼吸空间；商业摄影级布光，锐利清晰的微细节，真实的材质反光与接触阴影，自然的高光过渡；整体干净、高级、有质感，能吸引点击。画面里不许出现任何文字、水印、边框或拼贴。
"""

TAIL = """【画面允许出现的物品白名单】
只允许出现：{allow}。
除此之外不许出现任何其它物品：不要加道具、不要加包装、不要加文字、不要加 logo。场景氛围只能靠背景材质、台面、灯光和色调来营造。
{tail}"""


# ────────────────────────── 工具 ──────────────────────────
def mime_of(path):
    h = open(path, "rb").read(12)
    if h[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if h[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if h[:4] == b"RIFF" and h[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"


def data_url(path):
    with open(path, "rb") as f:
        return "data:%s;base64,%s" % (mime_of(path), base64.b64encode(f.read()).decode())


def read_xlsx(path):
    """stdlib 读 xlsx（不依赖 openpyxl），返回 [ {列: 值}, ... ]。"""
    z = zipfile.ZipFile(path)
    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    shared = []
    if "xl/sharedStrings.xml" in z.namelist():
        for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall(ns + "si"):
            shared.append("".join(t.text or "" for t in si.iter(ns + "t")))
    rows = []
    for name in sorted(n for n in z.namelist() if re.match(r"xl/worksheets/sheet\d+\.xml$", n)):
        for row in ET.fromstring(z.read(name)).iter(ns + "row"):
            cells = {}
            for c in row.findall(ns + "c"):
                col = re.match(r"([A-Z]+)", c.get("r")).group(1)
                t, v, isel = c.get("t"), c.find(ns + "v"), c.find(ns + "is")
                if t == "s" and v is not None:
                    val = shared[int(v.text)]
                elif isel is not None:
                    val = "".join(x.text or "" for x in isel.iter(ns + "t"))
                else:
                    val = v.text if v is not None else ""
                cells[col] = val
            if cells:
                rows.append(cells)
    return rows


def find_sku_dirs(root):
    """递归找出含「产品图」「效果图」的 SKU 目录，返回 {SKU: (目录, 产品图名, 效果图名)}。"""
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        prod = [f for f in filenames if "产品" in f and not f.startswith(".")]
        eff = [f for f in filenames if "效果" in f and not f.startswith(".")]
        if prod and eff:
            out[os.path.basename(dirpath)] = (dirpath, prod[0], eff[0])
    return out


# ────────────────────────── 出图 ──────────────────────────
def build_prompt(s):
    return HEAD.format(identity=s["identity"], adapt=s["adapt"], camera=s["camera"]) + "\n" + \
        TAIL.format(allow=s["allow"], tail=s["tail"])


def need_key():
    if not API_KEY:
        sys.exit("没配置 API Key：export AXN_API_KEY=...（或 .axn.json 里指定 api_key_env）")


def request_image(images, prompt, size):
    need_key()
    payload = {
        "model": MODEL,
        "images": [{"image_url": data_url(p)} for p in images],
        "prompt": prompt,
        "size": size,
        "n": 1,
        "watermark": False,
        "prompt_extend": False,
        "response_format": "b64_json",
        "output_format": "jpeg",
    }
    req = urllib.request.Request(
        URL, data=json.dumps(payload).encode(),
        headers={"Authorization": "Bearer " + API_KEY,
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read().decode())


def extract(data):
    for it in (data.get("data") or data.get("images") or []):
        if isinstance(it, dict):
            if it.get("b64_json"):
                return base64.b64decode(it["b64_json"].split(",", 1)[-1]), None
            u = it.get("url") or it.get("image_url")
            if isinstance(u, dict):
                u = u.get("url")
            if u:
                with urllib.request.urlopen(
                        urllib.request.Request(u, headers={"User-Agent": "axn/1.0"}), timeout=180) as rr:
                    return rr.read(), None
    return None, json.dumps(data)[:600]


def render_one(spec, images, out_path, size):
    if os.path.exists(out_path) and os.path.getsize(out_path) > 20000:
        return "skip 已存在"
    prompt = build_prompt(spec)
    last = ""
    for attempt in range(1, ATTEMPTS + 1):
        t0 = time.time()
        try:
            img, err = extract(request_image(images, prompt, size))
            if img:
                with open(out_path, "wb") as f:
                    f.write(img)
                return "ok %.0fs %dKB" % (time.time() - t0, len(img) // 1024)
            last = "no-image: %s" % err
        except urllib.error.HTTPError as e:
            body = e.read().decode()[:200]
            last = "HTTP %d %s" % (e.code, body)
            if e.code in (400, 404, 422):
                last += "\n   提示：" + NEED_I2I
                break
            if e.code in (401, 403):
                break
        except Exception as e:
            last = "%s: %s" % (type(e).__name__, e)
        if attempt < ATTEMPTS:
            time.sleep(25 * attempt if "429" in last else 6)
    return "FAIL " + last


def cmd_render(a):
    specs = json.load(open(a.specs, encoding="utf-8"))["skus"]
    print("端点 %s\n模型 %s（必须是图生图模型）\n尺寸 %s" % (BASE_URL, MODEL, a.size), flush=True)
    cand_dir = a.cand_dir
    os.makedirs(cand_dir, exist_ok=True)
    found = find_sku_dirs(a.src)
    jobs = []
    for sku, variants in specs.items():
        if a.only and sku != a.only:
            continue
        if sku not in found:
            print("!! 找不到 %s 的产品图/效果图（--src %s）" % (sku, a.src))
            continue
        prod_dir, prod, eff = found[sku]
        for cand in sorted(variants):
            if a.cand and cand != a.cand:
                continue
            images = [os.path.join(prod_dir, prod), os.path.join(prod_dir, eff)]
            images += [os.path.join(prod_dir, r) for r in variants[cand].get("refs", [])]
            jobs.append((sku, cand, variants[cand], images,
                         os.path.join(cand_dir, "%s_%s.jpg" % (sku, cand))))
    print("待出 %d 张：%s" % (len(jobs), ", ".join("%s_%s" % (j[0], j[1]) for j in jobs)))
    if a.dry_run:
        for j in jobs:
            print("  [dry] %s_%s  参考图 %d 张  提示词 %d 字"
                  % (j[0], j[1], len(j[3]), len(build_prompt(j[2]))))
        return
    t0 = time.time()
    for i, (sku, cand, spec, images, out) in enumerate(jobs):
        if i:
            time.sleep(STAGGER)
        print("[%s_%s] %s" % (sku, cand, render_one(spec, images, out, a.size)), flush=True)
    print("=== 完成 %.1f 分钟 ===" % ((time.time() - t0) / 60))


def cmd_pick(a):
    os.makedirs(a.out, exist_ok=True)
    src = os.path.join(a.cand_dir, "%s_%s.jpg" % (a.sku, a.cand))
    if not os.path.exists(src):
        sys.exit("候选不存在: " + src)
    dst = os.path.join(a.out, a.sku + ".jpg")
    if os.path.exists(dst):
        os.makedirs(a.replaced, exist_ok=True)
        n = 1
        while os.path.exists(os.path.join(a.replaced, "%s_v%d.jpg" % (a.sku, n))):
            n += 1
        bak = os.path.join(a.replaced, "%s_v%d.jpg" % (a.sku, n))
        shutil.copy2(dst, bak)
        print("旧图 ->", bak)
    shutil.copy2(src, dst)
    print("定稿 ->", dst)


def cmd_scan(a):
    root = a.path
    found = find_sku_dirs(root)
    print("发现 %d 个 SKU：" % len(found))
    template = {}
    for sku, (d, prod, eff) in sorted(found.items()):
        print("  %s   %s" % (sku, d))
        template[sku] = {"A": {"identity": "", "camera": "", "adapt": "",
                               "allow": "", "forbid": "", "tail": ""}}
    for dirpath, _, filenames in os.walk(root):
        for f in filenames:
            if f.endswith(".xlsx") and not f.startswith("~"):
                print("\nExcel 方位需求 %s:" % os.path.join(dirpath, f))
                for row in read_xlsx(os.path.join(dirpath, f)):
                    if row.get("A") and row["A"] != "sku":
                        print("  %s  %s" % (row.get("A", ""), row.get("B", "")))
    print("\n--- specs 模板 ---")
    print(json.dumps({"skus": template}, ensure_ascii=False, indent=2)[:1200])


def cmd_clean(a):
    os.makedirs(a.work, exist_ok=True)
    moved = []
    for name in ("_candidates", "_replaced"):
        p = os.path.join(a.out, name)
        if os.path.isdir(p):
            dst = os.path.join(a.work, name.lstrip("_"))
            if os.path.exists(dst):
                shutil.rmtree(dst)
            shutil.move(p, dst)
            moved.append((p, dst))
    for dirpath, dirnames, _ in os.walk(a.root):
        for d in list(dirnames):
            if d == "__pycache__":
                shutil.rmtree(os.path.join(dirpath, d))
                moved.append((os.path.join(dirpath, d), "删"))
                dirnames.remove(d)
    for m in moved:
        print("清理:", m)
    left = sorted(f for f in os.listdir(a.out) if os.path.isfile(os.path.join(a.out, f)))
    print("output/ 现有 %d 个文件：%s" % (len(left), ", ".join(left)))


# ────────────────────────── VLM 辅助：自动起草 specs / 自检 ──────────────────────────


def chat(messages, max_tokens=2500, attempts=2):
    need_key()
    if not VLM:
        sys.exit("spec/auto 需要一个视觉模型来起草提示词：export AXN_VLM_MODEL=<看图模型>。"
                 "不配置也行，手写 _work/specs.json 后直接 render。")
    payload = {"model": VLM, "max_tokens": max_tokens, "messages": messages}
    last = None
    for i in range(attempts):
        try:
            req = urllib.request.Request(
                BASE_URL + "/chat/completions", data=json.dumps(payload).encode(),
                headers={"Authorization": "Bearer " + API_KEY,
                         "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=300) as r:
                data = json.loads(r.read().decode())
            msg = (data.get("choices") or [{}])[0].get("message") or {}
            return msg.get("content") or msg.get("reasoning") or ""
        except Exception as e:
            last = e
            time.sleep(3)
    raise last


def img_msg(path, text=None):
    parts = []
    if text:
        parts.append({"type": "text", "text": text})
    parts.append({"type": "image_url", "image_url": {"url": data_url(path)}})
    return {"role": "user", "content": parts}


def json_from(text):
    i, j = text.find("{"), text.rfind("}")
    if i < 0 or j < 0:
        raise ValueError("VLM 没返回 JSON: " + text[:200])
    return json.loads(text[i:j + 1])


def draft_spec(sku, prod, eff, hint, category=""):
    """让 VLM 看图起草六段规格（供人再改）。精度低于人工，仅作一键跑的兜底。"""
    pre = ("这是一个电商产品图，品类是「%s」。" % category) if category else "这是一个电商产品图。"
    ask = (pre + "逐项描述产品本身（用于喂给绘图模型的 identity 段）："
           "① 有哪些部件、各几件 ② 颜色与材质 ③ 表面纹理/花纹/冲孔 ④ 有没有印字，"
           "逐字母写出并说明符号形状（是线条还是实心、几个角几条边）。"
           "只写看得清的，看不清的宁可不写，不要猜用途，不要评价。中文，200~400 字。")
    ident = chat([img_msg(prod, ask)])
    scene = chat([
        img_msg(prod, "第一张是产品正图（唯一真实性来源）。"),
        img_msg(eff, "第二张是同款商品的效果参考：只借鉴它的机位、背景、光线、构图，产品本身不要照搬。"),
        {"role": "user", "content": "运营给的机位要求：%s\n\n"
         "请输出 JSON（不要多余文字），字段：\n"
         "camera: 机位与摆放/装配关系的完整描述，先定轴向与角度，再写贴合证据（谁压住谁、有无缝），"
         "再写遮挡关系，最后写相机角度与产品占画面比例；穿戴类必须交代人与背景的空间关系。300~500 字。\n"
         "adapt: 只解决主体会不会糊在背景里（背景材质+光源+不许变成什么颜色），120~200 字。\n"
         "allow: 画面允许出现的物品白名单，顿号分隔。\n"
         "forbid: 绝不允许出现的物品/错误，顿号分隔（把数量写成「第 N+1 个」）。\n"
         "tail: 最后一道锁（数量、正反面、颜色、印字拼写），80~150 字。" % (hint or "未指定，按产品最自然的电商展示机位")}])
    s = json_from(scene)
    s["identity"] = ident.strip()
    for k in ("camera", "adapt", "allow", "forbid", "tail"):
        s.setdefault(k, "")
    return s


def find_hint(src, sku):
    """从来货里的 Excel 找该 SKU 的机位要求。"""
    for dirpath, _, files in os.walk(src):
        for f in files:
            if f.endswith(".xlsx") and not f.startswith("~"):
                try:
                    for row in read_xlsx(os.path.join(dirpath, f)):
                        if row.get("A", "").strip() == sku:
                            return row.get("B", "").strip()
                except Exception:
                    pass
    return ""


def cmd_spec(a):
    found = find_sku_dirs(a.src)
    specs = {}
    if os.path.exists(a.specs):
        specs = json.load(open(a.specs, encoding="utf-8")).get("skus", {})
    for sku, (d, prod, eff) in sorted(found.items()):
        if a.only and sku != a.only:
            continue
        if sku in specs and not a.force:
            print("[%s] 已有 specs，跳过（--force 覆盖）" % sku)
            continue
        hint = find_hint(a.src, sku)
        cat = os.path.basename(os.path.dirname(d)).replace("图片方位需求", "")
        print("[%s] 起草中…（品类：%s  运营要求：%s）" % (sku, cat or "未知", hint or "无"))
        try:
            specs.setdefault(sku, {})["A"] = draft_spec(
                sku, os.path.join(d, prod), os.path.join(d, eff), hint, cat)
        except Exception as e:
            print("   !! 起草失败，跳过该 SKU：%s: %s" % (type(e).__name__, e))
            specs.get(sku, {}).pop("A", None)
            continue
        print("   identity 前 60 字：%s" % specs[sku]["A"]["identity"][:60])
    os.makedirs(os.path.dirname(os.path.abspath(a.specs)), exist_ok=True)
    json.dump({"skus": specs}, open(a.specs, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("specs -> %s（共 %d 个 SKU，建议人工过一遍 camera）" % (a.specs, len(specs)))


def cmd_auto(a):
    """一键：缺 specs 就起草 → 出图 → 定稿 A。"""
    if not os.path.exists(a.specs):
        cmd_spec(argparse.Namespace(src=a.src, specs=a.specs, only=a.only, force=False))
    specs = json.load(open(a.specs, encoding="utf-8"))["skus"]
    found = find_sku_dirs(a.src)
    os.makedirs(a.cand_dir, exist_ok=True)
    os.makedirs(a.out, exist_ok=True)
    ok = []
    for sku in sorted(specs):
        if a.only and sku != a.only:
            continue
        if sku not in found:
            print("!! %s 找不到产品图/效果图" % sku)
            continue
        d, prod, eff = found[sku]
        for cand in sorted(specs[sku]):
            spec = specs[sku][cand]
            out = os.path.join(a.cand_dir, "%s_%s.jpg" % (sku, cand))
            if os.path.exists(out) and os.path.getsize(out) > 20000 and not a.force:
                print("[%s_%s] 已有候选，跳过" % (sku, cand))
                continue
            images = [os.path.join(d, prod), os.path.join(d, eff)]
            images += [os.path.join(d, r) for r in spec.get("refs", [])]
            print("[%s_%s] %s" % (sku, cand, render_one(spec, images, out, a.size)), flush=True)
            time.sleep(STAGGER)
            ov = spec.get("overlay")
            if ov and os.path.exists(out):
                try:
                    cmd_overlay(argparse.Namespace(
                        base=out, out=out, text=ov["text"],
                        ink=ov.get("ink", 18), cap=ov.get("cap", 0.034),
                        len=ov.get("len", 0.40), heel=ov.get("heel", 0.30),
                        side=ov.get("side", 0.0), mask=ov.get("mask", "tan"),
                        blur=ov.get("blur", 0.8), angle=ov.get("angle"), wave=ov.get("wave", 0.19)))
                except SystemExit as e:
                    print("   叠印失败：%s" % e)
            if a.review and os.path.exists(out):
                try:
                    verdict = chat([img_msg(os.path.join(d, prod), "第一张是产品正图（真品）。"),
                                    img_msg(out, "第二张是准备上架的生成图。"),
                                    {"role": "user", "content": "逐项检查第二张：① 产品数量与部件是否与第一张一致 ② 有无悬空/穿插/装错 ③ 有无多余文字水印。"
                                     "只回一行：PASS 或 FAIL + 一句最重要的原因。"}], max_tokens=200)
                except Exception as e:
                    verdict = "SKIP 复核失败（%s）" % type(e).__name__
                print("   复核：%s" % verdict.strip().split("\n")[0][:120])
                if "FAIL" in verdict.upper() and not spec.get("_retried"):
                    spec["_retried"] = True
                    print("   重出一张…")
                    print("   ->", render_one(spec, images, out, a.size))
        first = sorted(specs[sku])[0]
        src_img = os.path.join(a.cand_dir, "%s_%s.jpg" % (sku, first))
        if os.path.exists(src_img):
            dst = os.path.join(a.out, sku + ".jpg")
            os.makedirs(a.out, exist_ok=True)
            if os.path.exists(dst):
                os.makedirs(a.replaced, exist_ok=True)
                n = 1
                while os.path.exists(os.path.join(a.replaced, "%s_v%d.jpg" % (sku, n))):
                    n += 1
                shutil.copy2(dst, os.path.join(a.replaced, "%s_v%d.jpg" % (sku, n)))
            shutil.copy2(src_img, dst)
            ok.append(dst)
    print("=== 完成，成品 %d 张 ===" % len(ok))
    for p in ok:
        print("  ", p)


# ────────────────────────── 叠印产品印字（丝网印刷质感） ──────────────────────────
FONTS = ["/System/Library/Fonts/Supplemental/Arial Bold.ttf",
         "/System/Library/Fonts/Supplemental/Arial.ttf",
         "/System/Library/Fonts/Helvetica.ttc",
         "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
         "C:/Windows/Fonts/arialbd.ttf"]


def load_font(size):
    for p in FONTS:
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    sys.exit("找不到粗体字库，把系统里的粗体路径加进脚本的 FONTS 列表")


def mask_pts(im, kind):
    """找产品主体像素点：tan=浅驼皮革，gray=浅灰件。用于拟合长轴。"""
    w, h = im.size
    px = im.load()
    pts = []
    for y in range(0, h, 2):
        for x in range(0, w, 2):
            r, g, b = px[x, y][:3]
            if kind == "tan":
                ok = r > 150 and r - b > 34 and 105 < g < 235
            else:
                ok = 120 < (r + g + b) / 3 < 245 and max(r, g, b) - min(r, g, b) < 22
            if ok:
                pts.append((x, y))
    return pts


def fit_axis(pts):
    """PCA 拟合长轴，返回 (cx, cy, theta, t0, t1, s0, s1)。"""
    n = len(pts)
    cx = sum(p[0] for p in pts) / n
    cy = sum(p[1] for p in pts) / n
    sxx = sum((p[0] - cx) ** 2 for p in pts) / n
    syy = sum((p[1] - cy) ** 2 for p in pts) / n
    sxy = sum((p[0] - cx) * (p[1] - cy) for p in pts) / n
    theta = 0.5 * math.atan2(2 * sxy, sxx - syy)
    if abs(math.sin(theta)) < abs(math.cos(theta)):      # 长轴偏竖直
        theta += math.pi / 2
    ct, st = math.cos(theta), math.sin(theta)
    ts = [(p[0] - cx) * ct + (p[1] - cy) * st for p in pts]
    ss = [-(p[0] - cx) * st + (p[1] - cy) * ct for p in pts]
    return cx, cy, theta, min(ts), max(ts), min(ss), max(ss)


def emblem(size, stroke, wave=0.19):
    """空心圆角方形波浪边印章（四角花瓣凸起、四边中间微凹）。"""
    S, big = 8, size * 8
    im = Image.new("L", (big + 2 * stroke * S, big + 2 * stroke * S), 255)
    d = ImageDraw.Draw(im)
    c = im.size[0] / 2
    R = big / 2 - stroke * S / 2
    pts = []
    for i in range(720):
        a = 2 * math.pi * i / 720
        r = R * (1 - wave * math.cos(4 * a))
        pts.append((c + r * math.cos(a), c + r * math.sin(a)))
    d.line(pts + [pts[0]], fill=0, width=int(stroke * S), joint="curve")
    return im.resize((im.size[0] // S, im.size[1] // S), Image.LANCZOS)


def text_strip(length_px, cap_px, parts, wave=0.19):
    """一行白底黑字的墨层。parts 里 '|' 表示插印章符号。"""
    S = 4
    fs = int(cap_px * 1.42 * S)
    font = load_font(fs)
    em = emblem(int(cap_px * 1.55 * S), max(2, int(cap_px * 0.090 * S)), wave)
    gap, left = int(cap_px * 0.55 * S), fs // 4
    widths = [(em.size[0] if p == "|" else font.getbbox(p)[2] - font.getbbox(p)[0]) for p in parts]
    W = left + sum(widths) + gap * (len(parts) - 1) + left
    asc, desc = font.getmetrics()
    H = max(int(cap_px * 1.9 * S), em.size[1] + 8, asc + desc + 8)
    im = Image.new("L", (W, H), 255)
    d = ImageDraw.Draw(im)
    base_y, x = (H - (asc + desc)) // 2, left
    for p, w in zip(parts, widths):
        if p == "|":
            patch = Image.new("L", (W, H), 255)
            patch.paste(em, (x, (H - em.size[1]) // 2))
            im = ImageChops.darker(im, patch)
            d = ImageDraw.Draw(im)          # darker() 返回新对象，必须重建
        else:
            d.text((x, base_y), p, font=font, fill=0)
        x += w + gap
    scale = length_px / W
    return im.resize((int(W * scale), max(1, int(H * scale))), Image.LANCZOS)


def cmd_overlay(a):
    """在干净底图上叠印产品自带印字：自动拟合长轴，从后跟往脚尖排字。"""
    if not HAVE_PIL:
        sys.exit("叠印需要 Pillow：pip install Pillow")
    base = Image.open(a.base).convert("RGB")
    W, H = base.size
    pts = mask_pts(base, a.mask)
    if len(pts) < 500:
        sys.exit("主体像素太少（%d），换 --mask tan|gray 或检查底图" % len(pts))
    cx, cy, theta, t0, t1, s0, s1 = fit_axis(pts)
    if a.angle is not None:
        theta = math.radians(a.angle)
    L = t1 - t0
    heel = max(t0, t1) if math.sin(theta) > 0 else min(t0, t1)
    sgn = 1 if heel == max(t0, t1) else -1

    parts = [seg if seg.strip() else "|" for seg in a.text.split("|")]
    strip = text_strip(a.len * L, a.cap * L, parts, a.wave)
    strip_r = strip.rotate(-math.degrees(theta), expand=True,
                           resample=Image.BICUBIC, fillcolor=255)   # fillcolor 否则留黑条
    ink = Image.new("L", (W, H), 255)
    t_c, s_c = heel - sgn * a.heel * L, a.side * (s1 - s0)
    ox = cx + t_c * math.cos(theta) - s_c * math.sin(theta)
    oy = cy + t_c * math.sin(theta) + s_c * math.cos(theta)
    ink.paste(strip_r, (int(ox - strip_r.width / 2), int(oy - strip_r.height / 2)))
    if a.blur:
        ink = ink.filter(ImageFilter.GaussianBlur(a.blur))
    ink = ink.point(lambda v: 255 - int((255 - v) * (255 - a.ink) / 255))
    ImageChops.multiply(base, Image.merge("RGB", (ink,) * 3)).save(a.out, quality=95)
    print("长轴 %.1f°  长度 %.0f  字串中心 (%.0f,%.0f) -> %s"
          % (math.degrees(theta), L, ox, oy, a.out))


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("scan")
    p.add_argument("path")
    p.set_defaults(fn=cmd_scan)

    p = sub.add_parser("render")
    p.add_argument("--specs", required=True)
    p.add_argument("--src", required=True, help="来货根目录（含各 SKU 子目录）")
    p.add_argument("--cand-dir", default=os.path.join("_work", "candidates"))
    p.add_argument("--only")
    p.add_argument("--cand")
    p.add_argument("--size", default=SIZE)
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(fn=cmd_render)

    p = sub.add_parser("pick")
    p.add_argument("--sku", required=True)
    p.add_argument("--cand", required=True)
    p.add_argument("--out", default="output")
    p.add_argument("--cand-dir", default=os.path.join("_work", "candidates"))
    p.add_argument("--replaced", default=os.path.join("_work", "replaced"))
    p.set_defaults(fn=cmd_pick)

    p = sub.add_parser("clean")
    p.add_argument("--root", default=".")
    p.add_argument("--out", default="output")
    p.add_argument("--work", default="_work")
    p.set_defaults(fn=cmd_clean)

    p = sub.add_parser("spec", help="用看图模型起草 specs（可再人工改）")
    p.add_argument("--src", required=True)
    p.add_argument("--specs", default=os.path.join("_work", "specs.json"))
    p.add_argument("--only")
    p.add_argument("--force", action="store_true")
    p.set_defaults(fn=cmd_spec)

    p = sub.add_parser("auto", help="一键：起草 specs → 出图 → 定稿")
    p.add_argument("--src", required=True)
    p.add_argument("--out", default="output")
    p.add_argument("--specs", default=os.path.join("_work", "specs.json"))
    p.add_argument("--cand-dir", default=os.path.join("_work", "candidates"))
    p.add_argument("--replaced", default=os.path.join("_work", "replaced"))
    p.add_argument("--only")
    p.add_argument("--size", default=SIZE)
    p.add_argument("--review", action="store_true", help="用 VLM 复核，FAIL 自动重出一次")
    p.add_argument("--force", action="store_true")
    p.set_defaults(fn=cmd_auto)

    p = sub.add_parser("overlay", help="给干净底图叠印产品印字（丝网印刷质感）")
    p.add_argument("--base", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--text", required=True, help='词与符号用 | 分隔，如 "COMFORT|INSOLE"')
    p.add_argument("--ink", type=int, default=18, help="墨色，0=纯黑")
    p.add_argument("--cap", type=float, default=0.034, help="字高 / 产品长度")
    p.add_argument("--len", type=float, default=0.40, help="整行长度 / 产品长度")
    p.add_argument("--heel", type=float, default=0.30, help="字串中心距后跟的距离比例")
    p.add_argument("--side", type=float, default=0.0, help="横向偏移比例")
    p.add_argument("--mask", default="tan", choices=["tan", "gray"])
    p.add_argument("--blur", type=float, default=0.8)
    p.add_argument("--angle", type=float, help="手动指定长轴角度（默认自动拟合）")
    p.add_argument("--wave", type=float, default=0.19, help="印章符号的波浪幅度")
    p.set_defaults(fn=cmd_overlay)

    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
