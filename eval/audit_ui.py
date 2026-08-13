"""生成任务筛查打分页面（eval/audit_report.html）。

数据源: eval/tasks.json + eval/audit_scores.json + eval/.sanity_cache.json
用法:   python -m eval.audit_ui   → 浏览器打开 eval/audit_report.html
打分:   页面内改分自动存 localStorage；导出 JSON 回填 audit_scores.json。
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

from eval.env import venv_key
from eval.verify import load_sanity_cache

OUT = "eval/audit_report.html"
OFFICIAL_CSV = "eval/official_annotations.csv"

DIFF_LABEL = {
    "<15 min fix": "易(<15min)",
    "15 min - 1 h": "中(15m-1h)",
    "1-4 hours": "难(1-4h)",
    ">4 hours": "极难(>4h)",
}


def _load_official() -> dict[str, dict]:
    p = Path(OFFICIAL_CSV)
    if not p.exists():
        return {}
    out: dict[str, dict] = {}
    for r in csv.DictReader(p.open(encoding="utf-8")):
        iid = r["instance_id"]
        diff_raw = r.get("difficulty", "")
        out[iid] = {
            "clarity": int(float(r.get("underspecified", 0) or 0)),
            "f2p_reach": int(float(r.get("false_negative", 0) or 0)),
            "difficulty": DIFF_LABEL.get(diff_raw, diff_raw),
            "filter_out": (r.get("filter_out", "false") or "").lower() == "true",
            "notes": (r.get("underspecified_notes", "") or "")[:160],
            "test_notes": (r.get("false_negative_notes", "") or "")[:160],
        }
    return out

# 每题中文速览（人工写的，帮助打分者理解 issue 在说什么）
SUMMARIES: dict[str, str] = {
    "astropy__astropy-14182": "表格库：RST 输出格式不支持多行表头（header_rows），传参直接报错，希望能支持",
    "astropy__astropy-14365": "表格库：QDP 文件读取假设命令必须大写，手写的小写命令（如 read serr）读不了",
    "matplotlib__matplotlib-18869": "绘图库：顶层只有 __version__ 字符串，希望加一个可比较的版本号元组 version_info",
    "mwaskom__seaborn-3010": "统计绘图：PolyFit 拟合遇到缺失数据（NaN）直接崩溃，应跳过缺失值",
    "pylint-dev__pylint-6506": "代码检查器：传不认识的参数（如 -Q）时打印一堆堆栈报错，应像 mypy 一样只给用法提示",
    "scikit-learn__scikit-learn-13497": "机器学习库：_estimate_mi 里拿数组和字符串 'auto' 做 == 比较，新版 numpy 会报错",
    "scikit-learn__scikit-learn-13584": "机器学习库：print_changed_only 模式下打印带 numpy 数组参数的估计器报错",
    "scikit-learn__scikit-learn-15512": "机器学习库：亲和传播聚类不收敛时返回了实际簇，文档说应返回空数组和 -1 标签",
    "sphinx-doc__sphinx-10325": "文档生成器：:inherited-members: 选项只支持单个类，应支持逗号分隔多个类",
    "sphinx-doc__sphinx-11445": "文档生成器：设置了 rst_prolog 后，以域指令（如 :mod:）开头的标题渲染丢失",
    "sphinx-doc__sphinx-7975": "文档生成器：特殊符号索引生成了两个同名 Symbols 段落，且链接都指向第一个",
    "sphinx-doc__sphinx-8273": "文档生成器：man 手册应生成到 man/man1 这类分段目录，现在全堆在 man/ 下",
    "sphinx-doc__sphinx-8595": "文档生成器：autodoc 忽略了空的 __all__，空列表时仍把模块所有成员都导出来",
    "sphinx-doc__sphinx-8721": "文档生成器：viewcode 在生成 epub 时也生成了模块源码页，应该默认关闭",
    "sphinx-doc__sphinx-8801": "文档生成器：只写类型注解（无赋值）的继承成员被当成未文档化而漏掉",
    "sympy__sympy-12419": "符号计算：单位矩阵的加法求和算成 0（Identity 矩阵参与加法/求和时元素和错误）",
    "sympy__sympy-13480": "符号计算：对 coth(log(tan(x))) 做 subs 替换某些整数值时报错",
    "sympy__sympy-13647": "符号计算：Matrix.col_insert() 插入列后行为不正确（0 行矩阵/插入位置问题）",
    "sympy__sympy-16792": "符号计算：autowrap/cython 后端在参数未出现在表达式里时失败（未用参数被丢弃）",
    "sympy__sympy-18189": "符号计算：diophantine 求不定方程解依赖符号顺序（permute=True 时结果不完整）",
    "sympy__sympy-20590": "符号计算：Symbol 实例自 1.7 起带 __dict__（不可哈希/序列化问题）",
    "sympy__sympy-21379": "符号计算：对某些特殊表达式做简单 subs() 意外抛 PolynomialError",
    "sympy__sympy-21612": "符号计算：LaTeX 解析分数缺括号导致表达式错误",
    "sympy__sympy-22714": "符号计算：evaluate(False) 下 simplify 报 Imaginary coordinates are not permitted",
    "sympy__sympy-23262": "符号计算：Python 代码打印器不处理单元素元组（打印成不带逗号的裸值）",
    "sympy__sympy-24066": "符号计算：SI 单位系统无法识别指数为无量纲（量纲收集错误）",
    "sympy__sympy-24213": "符号计算：量纲收集在加法中识别不了等价维度",
    "sympy__sympy-17139": "符号计算：simplify(cos(x)*I) 触发复数比较异常（fu.py 化简器崩溃）",
    "scikit-learn__scikit-learn-10297": "机器学习：RidgeClassifierCV 的 store_cv_values 参数行为错误",
    "scikit-learn__scikit-learn-13439": "机器学习：Pipeline 应实现 __len__（len() 调用失败）",
    "scikit-learn__scikit-learn-13496": "机器学习：IsolationForest 应支持 warm_start（增量拟合）",
    "scikit-learn__scikit-learn-14894": "机器学习：SVM 稀疏拟合在 support_vectors_ 为空时除零崩溃",
    "scikit-learn__scikit-learn-25747": "机器学习：FeatureUnion 配 pandas 输出时聚合失败",
    "scikit-learn__scikit-learn-13142": "机器学习：GaussianMixture 的 predict 与 fit_predict 在 n_init>1 时结果不一致",
    "scikit-learn__scikit-learn-13779": "机器学习：Voting 估计器在传 weights 且某估计器为 None 时 fit 崩溃",
    "astropy__astropy-12907": "表格库：嵌套 CompoundModel 的 separability_matrix 计算错误",
    "pytest-dev__pytest-7432": "测试框架：--runxfail 与 skip 标记叠加时 skip 原因定位报告错误（打 xfail 又 skip 的测试）",
    "sympy__sympy-12171": "符号计算：Mathematica 代码打印器对浮点数和导数输出错误",
    "sympy__sympy-12481": "符号计算：Permutation 构造器收到非不相交的循环时抛错（本应合并）",
    "sympy__sympy-13031": "符号计算：稀疏矩阵 hstack/vstack 对 0×n 矩阵的行为与 1.1 版不一致",
    "sympy__sympy-13043": "符号计算：intpoly.decompose 返回元素无序的列表，应改为确定性顺序（集合）",
    "sympy__sympy-15345": "符号计算：mathematica_code 对 Max/Min 表达式输出错误",
    "sympy__sympy-15678": "符号计算：idiff 隐函数求导在函数表达式/多变量场景下结果不对",
    "sympy__sympy-17022": "符号计算：lambdify 对矩阵表达式（如 M+I）生成错误的 numpy 代码",
    "sympy__sympy-21614": "符号计算：Derivative 的 kind 属性标错（矩阵导数应是 MatrixKind）",
    "sympy__sympy-21847": "符号计算：itermonomials 带 min_degrees 参数时漏掉部分单项式",
    "sympy__sympy-22005": "符号计算：solve_poly_system 对多余变量/矛盾方程应抛错而不是卡住",
    "sympy__sympy-23117": "符号计算：Array([]) 空数组创建失败，而 Matrix([]) 可以",
    "sympy__sympy-23191": "符号计算：终端里 pretty_print 向量对象显示错乱（Unicode 对齐问题）",
    "sympy__sympy-24102": "符号计算：parse_mathematica 解析不了希腊字母等 Unicode 字符",
    "sympy__sympy-24152": "符号计算：TensorProduct 的 expand 展开 bug（负号/系数处理错误）",
    "sympy__sympy-24909": "符号计算：毫(milli)前缀运算行为错误（m*W 结果不对）",
}


def _env_state(task: dict, sanity: dict[str, bool]) -> str:
    key = venv_key(task)
    if key in sanity:
        return "passed" if sanity[key] else "broken"
    return "not_curated"


def build_data() -> dict:
    tasks = json.loads(Path("eval/tasks.json").read_text(encoding="utf-8"))
    scores_path = Path("eval/audit_scores.json")
    scores: dict[str, dict] = {}
    if scores_path.exists():
        raw = json.loads(scores_path.read_text(encoding="utf-8"))
        for iid, s in raw.items():
            scores[iid] = {k: v for k, v in s.items() if k != "_context"}
    sanity = load_sanity_cache()
    official = _load_official()
    return {
        "tasks": [{
            "instance_id": t["instance_id"],
            "repo": t.get("repo", ""),
            "summary": SUMMARIES.get(t["instance_id"], ""),
            "problem_statement": t.get("problem_statement", ""),
            "f2p": t.get("FAIL_TO_PASS", []),
            "p2p": t.get("PASS_TO_PASS", []),
            "official": official.get(t["instance_id"]),
        } for t in tasks],
        "scores": scores,
        "env": {t["instance_id"]: _env_state(t, sanity) for t in tasks},
    }


TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>vague-code 任务集筛查打分</title>
<style>
:root {
  --bg: #0f1115; --card: #171a21; --card2: #1d212b; --line: #2a2f3a;
  --fg: #e6e9ef; --dim: #8b93a3; --accent: #4f8cff; --ok: #3fb96b; --bad: #e5534b;
  --warn: #d29922;
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--fg);
  font: 14px/1.6 "Segoe UI", "Microsoft YaHei", system-ui, sans-serif; }
header { position: sticky; top: 0; z-index: 10; background: rgba(15,17,21,.96);
  border-bottom: 1px solid var(--line); padding: 12px 20px; display: flex;
  gap: 16px; align-items: center; flex-wrap: wrap; }
header h1 { font-size: 16px; margin: 0; }
.progress { flex: 1; min-width: 160px; }
.bar { height: 6px; background: var(--line); border-radius: 3px; overflow: hidden; margin-top: 4px; }
.bar > div { height: 100%; background: var(--accent); transition: width .2s; }
.btn { background: var(--card2); color: var(--fg); border: 1px solid var(--line);
  border-radius: 6px; padding: 6px 12px; cursor: pointer; font-size: 13px; }
.btn:hover { border-color: var(--accent); }
.btn.primary { background: var(--accent); border-color: var(--accent); color: #fff; }
main { max-width: 1080px; margin: 0 auto; padding: 20px; }
.criteria { background: var(--card); border: 1px solid var(--line); border-radius: 10px;
  padding: 14px 18px; margin-bottom: 16px; }
.criteria h2 { font-size: 14px; margin: 0 0 8px; }
.criteria table { width: 100%; border-collapse: collapse; font-size: 13px; }
.criteria td, .criteria th { border: 1px solid var(--line); padding: 6px 10px; vertical-align: top; }
.criteria th { color: var(--dim); font-weight: 600; white-space: nowrap; }
.toolbar { display: flex; gap: 8px; margin-bottom: 12px; flex-wrap: wrap; align-items: center; }
.chip { display: inline-block; padding: 1px 8px; border-radius: 10px; font-size: 12px;
  border: 1px solid var(--line); color: var(--dim); margin-left: 6px; }
.chip.passed { color: var(--ok); border-color: var(--ok); }
.chip.broken { color: var(--bad); border-color: var(--bad); }
.chip.not_curated { color: var(--warn); border-color: var(--warn); }
.card { background: var(--card); border: 1px solid var(--line); border-radius: 10px;
  padding: 14px 18px; margin-bottom: 12px; }
.card.done { border-left: 3px solid var(--accent); }
.card.excluded { border-left: 3px solid var(--bad); }
.card h3 { font-size: 14px; margin: 0 0 6px; font-family: Consolas, monospace; word-break: break-all; }
.meta { color: var(--dim); font-size: 12px; margin-bottom: 8px; }
.summary { background: rgba(79,140,255,.08); border: 1px solid rgba(79,140,255,.25);
  border-radius: 6px; padding: 6px 10px; font-size: 13px; margin-bottom: 8px; color: #cfe0ff; }
.official { background: rgba(63,185,107,.06); border: 1px solid rgba(63,185,107,.22);
  border-radius: 6px; padding: 6px 10px; font-size: 12px; margin-bottom: 8px; color: var(--dim); }
.official b { color: var(--fg); }
.os { font-weight: 600; }
.os.ok { color: var(--ok); } .os.bad { color: var(--bad); }
.onotes { margin-top: 2px; color: var(--dim); font-size: 11px; }
.problem { background: var(--card2); border-radius: 6px; padding: 8px 12px;
  max-height: 120px; overflow: hidden; white-space: pre-wrap; font-size: 13px;
  position: relative; margin-bottom: 8px; }
.problem.open { max-height: none; }
.problem .fade { position: absolute; bottom: 0; left: 0; right: 0; height: 40px;
  background: linear-gradient(transparent, var(--card2)); }
.toggle { color: var(--accent); cursor: pointer; font-size: 12px; user-select: none; }
.tests { margin-bottom: 10px; }
.tests .t { font-size: 12px; color: var(--dim); font-family: Consolas, monospace;
  background: var(--card2); border-radius: 4px; padding: 2px 8px; margin: 2px 4px 2px 0;
  display: inline-block; max-width: 100%; overflow: hidden; text-overflow: ellipsis;
  white-space: nowrap; vertical-align: middle; }
.score { display: flex; gap: 28px; flex-wrap: wrap; align-items: center; }
.dim { display: flex; align-items: center; gap: 8px; }
.dim label { font-size: 13px; color: var(--dim); }
.radios { display: flex; gap: 4px; }
.radios button { width: 34px; height: 30px; border-radius: 6px; border: 1px solid var(--line);
  background: var(--card2); color: var(--fg); cursor: pointer; font-size: 13px; }
.radios button:hover { border-color: var(--accent); }
.radios button.sel { background: var(--accent); border-color: var(--accent); color: #fff; }
.status { font-size: 12px; margin-left: auto; }
.status.keep { color: var(--ok); } .status.drop { color: var(--bad); }
footer { color: var(--dim); text-align: center; font-size: 12px; padding: 20px; }
</style>
</head>
<body>
<header>
  <h1>vague-code 任务集筛查打分</h1>
  <div class="progress"><span id="progText">0/0 已打分</span><div class="bar"><div id="progBar"></div></div></div>
  <button class="btn" id="importBtn">导入 JSON</button>
  <button class="btn" id="exportBtn">导出 JSON</button>
  <button class="btn primary" id="resetBtn">清空打分</button>
</header>
<main>
  <section class="criteria">
    <h2>筛查判定标准（SWE-bench Verified 标注法，0-3）</h2>
    <table>
      <tr><th>维度</th><th>0</th><th>1</th><th>2</th><th>3</th></tr>
      <tr>
        <th>问题清晰度 clarity</th>
        <td>无需追问即可开工</td><td>少量留白但有合理解释</td>
        <td>有歧义需判断</td><td>几乎无法理解</td>
      </tr>
      <tr>
        <th>F2P 可达性 f2p_reach</th>
        <td>测试完美覆盖所有解</td><td>覆盖多数解</td>
        <td>会误杀合理实现</td><td>与 issue 无关/可钻空子</td>
      </tr>
      <tr><th>环境 env（自动）</th><td colspan="4">passed = venv 可搭 + sanity 双检通过；broken = 双检失败；not_curated = 待策展</td></tr>
    </table>
    <p style="color:var(--dim);font-size:12px;margin:8px 0 0">
      官方标注来自 OpenAI SWE-bench Verified 人工评审（1699 抽样），已预填为你默认的打分；
      你不认可可改，改后存本浏览器。剔除规则: clarity ≥ 2 或 f2p_reach ≥ 2 或 env = broken。
      完成后点「导出 JSON」覆盖 eval/audit_scores.json。
    </p>
  </section>
  <div class="toolbar">
    <button class="btn" data-filter="all">全部</button>
    <button class="btn" data-filter="todo">未打分</button>
    <button class="btn" data-filter="done">已打分</button>
    <button class="btn" data-filter="drop">剔除</button>
    <button class="btn" data-filter="env">环境可跑</button>
  </div>
  <div id="cards"></div>
  <footer>vague-code eval · 0016 计划 P0-5 任务筛查</footer>
</main>
<script>
const DATA = __DATA__;
const LS_KEY = "vaguecode_audit_scores_v1";
const scores = loadScores();

function loadScores() {
  try {
    const raw = localStorage.getItem(LS_KEY);
    if (raw) return JSON.parse(raw);
  } catch (e) {}
  const base = {};
  for (const t of DATA.tasks) {
    if (t.official) {
      base[t.instance_id] = { clarity: t.official.clarity, f2p_reach: t.official.f2p_reach };
    }
  }
  return base;
}
function save() { localStorage.setItem(LS_KEY, JSON.stringify(scores)); }
function envOf(id) { return (DATA.env || {})[id] || "not_curated"; }
function excluded(id) {
  const s = scores[id] || {};
  const c = s.clarity ?? 0, f = s.f2p_reach ?? 0;
  return c >= 2 || f >= 2 || envOf(id) === "broken";
}
function scored(id) {
  const s = scores[id];
  return !!(s && Number.isInteger(s.clarity) && Number.isInteger(s.f2p_reach));
}

const esc = (s) => (s || "").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;").replace(/'/g,"&#39;");
const shortTest = (t) => t.split("::").pop() + " · " + t.split("::")[0].split("/").pop();

function render() {
  const root = document.getElementById("cards");
  root.innerHTML = "";
  let done = 0;
  for (const t of DATA.tasks) {
    const id = t.instance_id;
    const s = scores[id] || {};
    const sc = scored(id); if (sc) done++;
    const ex = excluded(id);
    const env = envOf(id);
    const cls = ["card", sc ? "done" : "", ex ? "excluded" : ""].join(" ");
    const p2pShort = t.p2p.slice(0, 4).map(shortTest);
    const moreP2P = t.p2p.length > 4 ? ` +${t.p2p.length - 4} 个` : "";
    root.insertAdjacentHTML("beforeend", `
    <div class="${cls}" data-id="${esc(id)}">
      <h3>${esc(id)}<span class="chip ${env}">${env}</span></h3>
      <div class="meta">${esc(t.repo)} · F2P ${t.f2p.length} · P2P ${t.p2p.length}</div>
      ${t.summary ? `<div class="summary">📖 ${esc(t.summary)}</div>` : ""}
      ${t.official ? `
      <div class="official">
        <b>官方人工标注（OpenAI SWE-bench Verified 评审）</b>
        清晰度 <span class="os ${t.official.clarity >= 2 ? "bad" : "ok"}">${t.official.clarity}/3</span>
        · 测试有效性 <span class="os ${t.official.f2p_reach >= 2 ? "bad" : "ok"}">${t.official.f2p_reach}/3</span>
        · 难度 ${esc(t.official.difficulty)}
        · 官方判定 <span class="os ${t.official.filter_out ? "bad" : "ok"}">${t.official.filter_out ? "✗ 剔除" : "✓ 保留"}</span>
        ${t.official.notes ? `<div class="onotes">问题备注: ${esc(t.official.notes)}</div>` : ""}
        ${t.official.test_notes ? `<div class="onotes">测试备注: ${esc(t.official.test_notes)}</div>` : ""}
      </div>` : `<div class="official">无官方标注（OpenAI 1699 抽样未覆盖）</div>`}
      <div class="problem" data-open="0">${esc(t.problem_statement)}
        <div class="fade"></div></div>
      <div class="toggle" data-toggle>展开全文 / 收起</div>
      <div class="tests">
        <span style="color:var(--bad);font-size:12px">F2P</span>
        ${t.f2p.map(x => `<span class="t" title="${esc(x)}">${esc(shortTest(x))}</span>`).join("")}
        <span style="color:var(--dim);font-size:12px;margin-left:8px">P2P</span>
        ${p2pShort.map(x => `<span class="t" title="${esc(x)}">${esc(x)}</span>`).join("")}
        <span style="font-size:12px;color:var(--dim)">${moreP2P}</span>
      </div>
      <div class="score">
        <div class="dim"><label>问题清晰度</label><div class="radios" data-dim="clarity">${radio("clarity", s.clarity)}</div></div>
        <div class="dim"><label>F2P 可达性</label><div class="radios" data-dim="f2p_reach">${radio("f2p_reach", s.f2p_reach)}</div></div>
        <span class="status ${ex ? "drop" : "keep"}">${ex ? "⚠ 剔除" : "✓ 保留"}</span>
      </div>
    </div>`);
  }
  const total = DATA.tasks.length;
  document.getElementById("progText").textContent = done + "/" + total + " 已打分";
  document.getElementById("progBar").style.width = (done / total * 100) + "%";
  bindEvents();
}
function radio(dim, val) {
  return [0,1,2,3].map(v => `<button data-v="${v}" class="${val === v ? "sel" : ""}">${v}</button>`).join("");
}
function bindEvents() {
  document.querySelectorAll(".toggle").forEach(el => {
    el.onclick = () => {
      const p = el.previousElementSibling;
      const open = p.dataset.open === "1";
      p.dataset.open = open ? "0" : "1";
      p.classList.toggle("open", !open);
      el.textContent = open ? "展开全文 / 收起" : "收起全文";
    };
  });
  document.querySelectorAll(".radios").forEach(group => {
    group.onclick = (e) => {
      const btn = e.target.closest("button"); if (!btn) return;
      const card = group.closest(".card");
      const id = card.dataset.id;
      const dim = group.dataset.dim;
      scores[id] = scores[id] || {};
      scores[id][dim] = Number(btn.dataset.v);
      save(); render();
    };
  });
  document.querySelectorAll(".toolbar .btn").forEach(btn => {
    btn.onclick = () => applyFilter(btn.dataset.filter);
  });
}
function applyFilter(f) {
  const cards = document.querySelectorAll(".card");
  cards.forEach(c => {
    const id = c.dataset.id;
    let show = true;
    if (f === "todo") show = !scored(id);
    else if (f === "done") show = scored(id);
    else if (f === "drop") show = excluded(id);
    else if (f === "env") show = envOf(id) === "passed";
    c.style.display = show ? "" : "none";
  });
}
document.getElementById("exportBtn").onclick = () => {
  const out = {};
  for (const t of DATA.tasks) {
    const s = scores[t.instance_id] || {};
    out[t.instance_id] = { clarity: s.clarity ?? 0, f2p_reach: s.f2p_reach ?? 0 };
  }
  const blob = new Blob([JSON.stringify(out, null, 2)], { type: "application/json" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "audit_scores.json";
  a.click();
  URL.revokeObjectURL(a.href);
};
document.getElementById("importBtn").onclick = () => {
  const inp = document.createElement("input");
  inp.type = "file"; inp.accept = ".json";
  inp.onchange = () => {
    const f = inp.files[0]; if (!f) return;
    f.text().then(txt => {
      const data = JSON.parse(txt);
      for (const [id, s] of Object.entries(data)) {
        if (!DATA.tasks.some(x => x.instance_id === id)) continue;
        scores[id] = { clarity: s.clarity ?? 0, f2p_reach: s.f2p_reach ?? 0 };
      }
      save(); render();
      alert("已导入 " + Object.keys(data).length + " 条打分");
    });
  };
  inp.click();
};
document.getElementById("resetBtn").onclick = () => {
  if (!confirm("清空本浏览器保存的全部打分？")) return;
  Object.keys(scores).forEach(k => delete scores[k]);
  save(); render();
};
render();
</script>
</body>
</html>"""


def main() -> None:
    data = build_data()
    html = TEMPLATE.replace("__DATA__", json.dumps(data, ensure_ascii=False).replace("</", "<\\/"))
    Path(OUT).write_text(html, encoding="utf-8")
    scored = sum(1 for s in data["scores"].values()
                 if isinstance(s.get("clarity"), int) or isinstance(s.get("f2p_reach"), int))
    print(f"Generated {OUT} ({len(data['tasks'])} tasks, {scored} pre-scored)")


if __name__ == "__main__":
    main()
