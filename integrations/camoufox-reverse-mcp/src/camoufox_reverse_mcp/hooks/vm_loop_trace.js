/**
 * vm_loop_trace.js - 通用解释器循环 tick 运行时 + 动态代码注入器（第二阶段 Task 2）
 *
 * 注入路径：
 *   1. 静态：loop_rewriter.py 在 document/脚本响应里注入（本文件提供 tick 运行时）；
 *   2. 动态：本文件 hook 六类动态代码通道——eval、Function 族构造器
 *      （含 (fn).constructor 绕过与 async/generator 变体）、Trusted Types
 *      createPolicy（require-trusted-types-for 'script' 页面的落地通道）、
 *      script 元素 appendChild/insertBefore、Worker（只记录）、
 *      setTimeout/setInterval 字符串参数——覆盖 BotGuard 这类以字符串
 *      内嵌、运行时才物化的 VM 解释器。
 *   所有动态源码（无论是否注入成功）都记录 kind/长度/哈希/前缀到
 *   dynamic_sources，用于诊断代码物化走的是哪条通道。
 *
 * 注入形式（不依赖循环体形态，while(cond) -> while(tick(...))）：
 *   while (window.__mcp_vm_loop_tick("L<offset>", function(){return (cond);},
 *                                    function(){return [stateVars];}))
 * tick 返回 cond 原值，语义不变；对 while(x!=81) 形式自动快照被比较的变量。
 *
 * 模板变量:
 *   {{MAX_STATES}}   - 每个循环最多保存的状态快照数
 *   {{STATE_VARS}}   - JSON 数组：额外快照的变量名（空数组 = 只自动识别）
 *   {{SRC_FILTER}}   - 只处理包含该子串的动态源码（空串 = 全部）
 *   {{MAX_SOURCES}}  - 最多记录多少条动态源码元数据
 *   {{TICK_TIMES}}   - true 时每个状态快照附带相对首 tick 的毫秒时间戳
 *                      （与原生 PropertyTracer 事件同一 wall-clock 坐标系，
 *                      用于原生↔VM 时序关联；热循环有额外开销，默认关）
 *   {{CODE_PATCHES}} - JSON 数组：通用代码补丁（第四阶段 Task 5），元素
 *                      {name, pattern, replacement, flags?}，编译为 RegExp
 *                      后按序应用在 tick 插桩之后（无循环的源码也应用）。
 *                      补丁产物约定写入 globalThis.__mcp_vm_counters，
 *                      drain 时随各 realm 一并回收。
 *
 * 读取: window.__mcp_vm_loop_state.drain()
 */
(function __mcp_vm_loop_install(win) {
    var window = win;  // 其余代码统一走 window.*，便于装入任意 realm
    if (window.__mcp_vm_loop_tick) return;
    // 自身源码：用于向 BotGuard 新建的同源 iframe realm 与 Worker realm
    // 复制安装（globalThis 在 window/worker 两种全局下都成立）
    var SELF_SRC = '(' + __mcp_vm_loop_install.toString() + ')(globalThis);';
    // 原生 toString 伪装守护（第十一阶段，gsxt 加速乐实测缺口）：反爬 VM
    // 用 Function.prototype.toString.call(fn) 识别包装函数——它会绕过函数
    // 自带的 toString 属性，自有属性伪装无效。WeakMap 登记「假函数→原生
    // 源码串」，守护版 toString 命中即返回原生串；未命中走原实现。
    var _FP_toString = window.Function && window.Function.prototype &&
        window.Function.prototype.toString;
    // 登记表跨 hook 共享（fetch/xhr 钩子复用同一张 WeakMap），避免重复
    // 包裹 toString 形成多层代理。
    var fakeFns = window.__mcp_fake_fn_src ||
        (typeof WeakMap !== 'undefined' ? new WeakMap() : null);
    window.__mcp_fake_fn_src = fakeFns;
    function disguise(fake, original, fallback) {
        // name/length 也易被指纹校验（fetch.length 原生 1、包装 2 这类
        // 差异会被加速乐遥测判改机），与原函数对齐；普通函数自带的
        // prototype 自有属性（不可配置删不掉）值对齐原生——非构造器
        // 原生函数没有 prototype。
        if (original) {
            try {
                Object.defineProperty(fake, 'name',
                    { value: original.name, configurable: true });
            } catch (e) {}
            try {
                Object.defineProperty(fake, 'length',
                    { value: original.length, configurable: true });
            } catch (e) {}
            try {
                if (!Object.prototype.hasOwnProperty.call(original, 'prototype') &&
                    Object.prototype.hasOwnProperty.call(fake, 'prototype'))
                    fake.prototype = undefined;
            } catch (e) {}
        }
        if (!fakeFns || !_FP_toString) return;
        var src = fallback;
        try { src = _FP_toString.call(original); } catch (e) {}
        try { fakeFns.set(fake, src); } catch (e) {}
    }
    // 重复安装检测不用自有标记属性（getOwnPropertyNames 可枚举），
    // 直接查登记表。
    if (fakeFns && _FP_toString &&
        !fakeFns.has(window.Function.prototype.toString)) {
        var guardedToString = function() {
            try {
                if (fakeFns.has(this)) return fakeFns.get(this);
            } catch (e) {}
            return _FP_toString.call(this);
        };
        try { guardedToString.prototype = undefined; } catch (e) {}
        Object.defineProperty(guardedToString, 'name',
            { value: 'toString', configurable: true });
        var selfSrc;
        try { selfSrc = _FP_toString.call(_FP_toString); } catch (e) {
            selfSrc = 'function toString() { [native code] }';
        }
        fakeFns.set(guardedToString, selfSrc);
        window.Function.prototype.toString = guardedToString;
    }
    var MAX_STATES = {{MAX_STATES}};
    var STATE_VARS = {{STATE_VARS}};
    var SRC_FILTER = {{SRC_FILTER}};
    var MAX_SOURCES = {{MAX_SOURCES}};
    var TICK_TIMES = {{TICK_TIMES}};
    var CODE_PATCHES = {{CODE_PATCHES}};
    var loops = {};
    var sources = [];
    var tickCalls = 0;
    var overheadNs = 0;
    var _eval = window.eval;
    var _Function = window.Function;

    // 通用代码补丁（第四阶段 Task 5）：正则→替换对，编译失败的条目跳过
    // 不阻断其余补丁与插桩。
    var PATCH_LIST = [];
    for (var pi = 0; pi < CODE_PATCHES.length; pi++) {
        try {
            var cp = CODE_PATCHES[pi];
            var flags = cp.flags || '';
            // structural 补丁需要捕获组偏移（/d 指数），引擎不支持则跳过该条
            if (cp.structural && flags.indexOf('d') === -1) flags += 'd';
            PATCH_LIST.push({
                name: String(cp.name || ('patch' + pi)),
                re: new RegExp(cp.pattern, flags),
                replacement: String(cp.replacement),
                structural: cp.structural === true
            });
        } catch (e) {}
    }

    // 结构签名锚点（第十阶段切片 A）：等长归一化——把源码中非关键字、
    // 非属性访问（前面不是 '.'）的标识符逐字符替换为等长的 '_'，字符串/
    // 注释/正则字面量原样跳过。位置 1:1 不变，因此归一化文本上的 match/
    // group span 可以直接切回原源码恢复真实名字。效果：局部变量与函数
    // 改名（混淆器最常见手法）不影响命中；属性改名仍会失配（如实限制）。
    // 使用方式：补丁对象加 "structural": true，pattern 针对归一化形态
    // 书写——标识符一律写 _+，关键字（function/while/return 等）保持
    // 字面量，空白用 \s*；replacement 里 $1..$9 恢复原名。
    var JS_KEYWORDS = ('break case catch class const continue debugger default ' +
        'delete do else export extends finally for function if import in ' +
        'instanceof new return super switch this throw try typeof var void ' +
        'while with yield let static get set async await of from ' +
        'null true false undefined arguments').split(' ');
    var JS_KW_SET = {};
    for (var ki = 0; ki < JS_KEYWORDS.length; ki++) JS_KW_SET[JS_KEYWORDS[ki]] = 1;

    function normalizeIdentifiers(src) {
        var out = src.split('');
        var i = 0, n = src.length;
        var prevSig = '';
        while (i < n) {
            var c = src[i];
            if (c === '"' || c === "'" || c === '`') {
                var q = c;
                out[i++] = c;
                while (i < n && src[i] !== q) {
                    if (src[i] === '\\') i++;
                    i++;
                }
                i++;
                prevSig = q;
                continue;
            }
            if (c === '/' && src[i + 1] === '/') {
                while (i < n && src[i] !== '\n') i++;
                continue;
            }
            if (c === '/' && src[i + 1] === '*') {
                i += 2;
                while (i < n && !(src[i] === '*' && src[i + 1] === '/')) i++;
                i += 2;
                continue;
            }
            if (c === '/' && '(,=:[!&|?{};+-*%~^<>'.indexOf(prevSig) !== -1) {
                i++;
                while (i < n && src[i] !== '/') {
                    if (src[i] === '\\') i++;
                    if (src[i] === '[') while (i < n && src[i] !== ']') { if (src[i] === '\\') i++; i++; }
                    i++;
                }
                i++;
                prevSig = '/';
                continue;
            }
            if (/[A-Za-z_$]/.test(c)) {
                var j = i + 1;
                while (j < n && /[A-Za-z0-9_$]/.test(src[j])) j++;
                var name = src.substring(i, j);
                // 属性名（.foo）与关键字保持原样
                if (prevSig !== '.' && !JS_KW_SET[name]) {
                    for (var k2 = i; k2 < j; k2++) out[k2] = '_';
                }
                prevSig = 'x';
                i = j;
                continue;
            }
            if (!/\s/.test(c)) prevSig = c;
            i++;
        }
        return out.join('');
    }

    // 归一化文本上跑正则，$1..$9 用捕获组 span 切回原源码恢复真名。
    function applyStructuralPatch(src, p) {
        var norm = normalizeIdentifiers(src);
        var re = p.re;
        re.lastIndex = 0;
        var out = '', last = 0, hit = false, m;
        while ((m = re.exec(norm)) !== null) {
            hit = true;
            var rep = p.replacement.replace(/\$\$|\$(\d)/g, function(tok, gi) {
                if (tok === '$$') return '$';
                var idx = Number(gi);
                if (!m.indices || !m.indices[idx]) return '';
                var span = m.indices[idx];
                if (span[0] < 0) return '';
                return src.substring(span[0], span[1]);
            });
            out += src.substring(last, m.index) + rep;
            last = m.index + m[0].length;
            if (!re.global) break;
            if (m[0].length === 0) re.lastIndex++;
        }
        out += src.substring(last);
        return { src: out, hit: hit };
    }

    function applyCodePatches(src) {
        var applied = [];
        var out = src;
        for (var i = 0; i < PATCH_LIST.length; i++) {
            var p = PATCH_LIST[i];
            try {
                if (p.structural) {
                    var r = applyStructuralPatch(out, p);
                    if (r.hit) { out = r.src; applied.push(p.name); }
                } else if (p.re.test(out)) {
                    out = out.replace(p.re, p.replacement);
                    applied.push(p.name);
                }
            } catch (e) {}
        }
        return { src: out, applied: applied };
    }

    // 捕获原始 charCodeAt：第十二阶段 hook 11 包装 String.prototype
    // .charCodeAt 后，hashOf 仍是自身热路径——必须走原始实现，否则每次
    // 哈希都自产「逐字符读取」合并事件（自污染）。
    var _charCodeAt0 = window.String && window.String.prototype &&
        window.String.prototype.charCodeAt;
    function hashOf(s) {
        var h = 0;
        for (var i = 0; i < s.length; i++)
            h = (h * 31 + (_charCodeAt0 ? _charCodeAt0.call(s, i)
                                        : s.charCodeAt(i))) | 0;
        return (h >>> 0).toString(16);
    }

    window.__mcp_vm_loop_tick = function(id, condFn, varsFn) {
        var isFn = typeof condFn === 'function';
        var cond = isFn ? condFn() : true;
        var L = loops[id];
        if (!L) L = loops[id] = { n: 0, t0: Date.now(), t1: 0, states: [],
                                  truncated: false, times: null, rt0: 0 };
        L.n++;
        // 热循环快路径（第十一阶段，gsxt 加速乐实测）：混淆挑战的紧循环
        // 单循环可达千万级迭代，若每次都 varsFn() 快照 + 双 performance.now()
        // 测开销，几十倍减速会把挑战拖死。快照装满后只计数、不再求值
        // varsFn；开销计时也只测每循环前 64 次。
        if (L.truncated) { tickCalls++; return cond; }
        L.t1 = Date.now();
        var t0 = (L.n <= 64 && typeof performance !== 'undefined' &&
                  performance.now) ? performance.now() : 0;
        var snapshot;
        if (isFn && varsFn) {
            try { snapshot = varsFn(); } catch (e) { snapshot = { error: String(e) }; }
        } else if (!isFn && condFn != null) {
            snapshot = condFn;
        }
        if (snapshot !== null && snapshot !== undefined) {
            if (L.states.length < MAX_STATES) L.states.push(snapshot);
            else L.truncated = true;
        }
        if (TICK_TIMES && t0) {
            if (L.times === null) { L.times = []; L.rt0 = t0; }
            // 相对首 tick 的毫秒（µs 精度保留三位小数），与 states 平行对齐
            if (L.times.length < MAX_STATES)
                L.times.push(Math.round((t0 - L.rt0) * 1000) / 1000);
        }
        tickCalls++;
        if (t0) overheadNs += (performance.now() - t0) * 1e6;
        return cond;
    };

    // 扫描源码，跳过字符串/注释/正则，返回 while/for 循环测试表达式的区间列表。
    // while: span={kind:'while', start, testStart, testEnd, end}
    // for:   span={kind:'for', start, pre, post, testStart, testEnd, end, hasTest}
    //        pre=INIT+';'，post=';'+UPDATE；for-of/in 无顶层层分号，自动跳过。
    function scanWhileTests(src) {
        var spans = [];
        var i = 0, n = src.length;
        var prevSig = '';  // 上一个有意义字符，用于区分正则与除法
        while (i < n) {
            var c = src[i];
            if (c === '"' || c === "'" || c === '`') {
                var q = c;
                i++;
                while (i < n && src[i] !== q) {
                    if (src[i] === '\\') i++;
                    i++;
                }
                i++;
                prevSig = q;
                continue;
            }
            if (c === '/' && src[i + 1] === '/') {
                while (i < n && src[i] !== '\n') i++;
                continue;
            }
            if (c === '/' && src[i + 1] === '*') {
                i += 2;
                while (i < n && !(src[i] === '*' && src[i + 1] === '/')) i++;
                i += 2;
                continue;
            }
            if (c === '/' && '(,=:[!&|?{};+-*%~^<>'.indexOf(prevSig) !== -1) {
                // 正则字面量（启发式）
                i++;
                while (i < n && src[i] !== '/') {
                    if (src[i] === '\\') i++;
                    if (src[i] === '[') while (i < n && src[i] !== ']') { if (src[i] === '\\') i++; i++; }
                    i++;
                }
                i++;
                prevSig = '/';
                continue;
            }
            var kw = null;
            if (c === 'w' && src.substr(i, 5) === 'while' &&
                !/[A-Za-z0-9_$]/.test(src[i - 1] || ' ') &&
                !/[A-Za-z0-9_$]/.test(src[i + 5] || ' ')) kw = 'while';
            else if (c === 'f' && src.substr(i, 3) === 'for' &&
                !/[A-Za-z0-9_$]/.test(src[i - 1] || ' ') &&
                !/[A-Za-z0-9_$]/.test(src[i + 3] || ' ')) kw = 'for';
            if (kw) {
                var j = i + kw.length;
                while (j < n && /\s/.test(src[j])) j++;
                if (src[j] === '(') {
                    var depth = 0, k = j;
                    var q2 = '';
                    var semi1 = -1, semi2 = -1;
                    var braceDepth = 0, sawBrace = false;
                    while (k < n) {
                        var ch = src[k];
                        if (q2) {
                            if (ch === '\\') k++;
                            else if (ch === q2) q2 = '';
                        } else if (ch === '"' || ch === "'" || ch === '`') {
                            q2 = ch;
                        } else if (ch === '(') depth++;
                        else if (ch === ')') {
                            depth--;
                            if (depth === 0) break;
                        } else if (ch === '{') { braceDepth++; sawBrace = true; }
                        else if (ch === '}') { if (braceDepth > 0) braceDepth--; }
                        else if (ch === ';' && depth === 1 && braceDepth === 0) {
                            // 分号只在「paren 第一层且无大括号包裹」时才算
                            // for 头分隔符——init 里的函数表达式（reCAPTCHA
                            // 实测：for(R=function(){for(;;){...}},k=0;;)）
                            // 内部的 for 分号不算数。
                            if (semi1 < 0) semi1 = k; else if (semi2 < 0) semi2 = k;
                        }
                        k++;
                    }
                    if (depth === 0) {
                        if (kw === 'while') {
                            spans.push({ kind: 'while', start: i,
                                testStart: j + 1, testEnd: k, end: k + 1 });
                            i = k + 1;
                            prevSig = ')';
                            continue;
                        } else if (sawBrace) {
                            // for 头里带语句块（函数表达式等）：保守跳过
                            // 外层，从 '(' 后继续扫描让内层循环正常命中。
                            i = j + 1;
                            prevSig = '(';
                            continue;
                        } else if (semi1 >= 0 && semi2 >= 0) {
                            // 经典 for(init;test;update)；for-of/in 走不到这里
                            spans.push({ kind: 'for', start: i,
                                pre: src.substring(j + 1, semi1 + 1),
                                post: src.substring(semi2, k + 1),
                                testStart: semi1 + 1, testEnd: semi2,
                                end: k + 1 });
                            i = k + 1;
                            prevSig = ')';
                            continue;
                        }
                        i = k + 1;
                        prevSig = ')';
                        continue;
                    }
                }
            }
            if (!/\s/.test(c)) prevSig = c;
            i++;
        }
        return spans;
    }

    // 识别 while(x!=81) / while(x===3) 形式中的状态变量名
    function stateVarOf(test) {
        var m = /^\s*([A-Za-z_$][A-Za-z0-9_$]*)\s*(?:!==?|===?)\s*\d+\s*$/.exec(test);
        return m ? m[1] : null;
    }

    // VM 解释器启发：循环体紧跟 switch(X) 时把判别变量 X 加入快照
    function switchVarAfter(src, end) {
        var tail = src.substring(end, end + 300);
        var m = /^\s*(?:\{|try\s*\{|\s)*\s*switch\s*\(\s*([A-Za-z_$][A-Za-z0-9_$]*)\s*\)/
            .exec(tail);
        return m ? m[1] : null;
    }

    // 控制流平坦化 FSM 变量识别（BotGuard 解释器形态：
    // for(N=89,T=93;;)try{if(N==78)... / for(x=(C=34,92);;)try{if(C==...)
    // 循环体头部（跳过 { 与 try{）第一个 if(X==数字)/switch(X) 的判别变量 X；
    // for 循环要求 X 在 init 里被赋值（逗号表达式嵌套赋值也算），
    // 避免把普通计数循环的局部 if 误判成派发。
    function fsmVarAfter(src, span) {
        var tail = src.substring(span.end, span.end + 400);
        var m = /^(?:\s|\{|try\s*\{)*?(?:if\s*\(\s*([A-Za-z_$][A-Za-z0-9_$]*)\s*==\s*\d|switch\s*\(\s*([A-Za-z_$][A-Za-z0-9_$]*)\s*\))/
            .exec(tail);
        if (!m) return null;
        var name = m[1] || m[2];
        if (span.kind === 'for') {
            var re = new RegExp('(?:^|[^A-Za-z0-9_$])' + name + '\\s*=[^=]');
            if (!re.test(span.pre || '')) return null;
        }
        return name;
    }

    function injectTicks(src) {
        if (typeof src !== 'string' || !src)
            return { src: src, count: 0, patches: [] };
        if (SRC_FILTER && src.indexOf(SRC_FILTER) === -1)
            return { src: src, count: 0, patches: [] };
        // 无循环的源码也要过代码补丁通道（Task 5）：补丁与循环插桩相互独立。
        var spans = (src.indexOf('while') === -1 && src.indexOf('for') === -1)
            ? [] : scanWhileTests(src);
        var srcTag = hashOf(src);  // 同一源码反复 eval 共享 id；不同源码不撞 id
        var out = '';
        var cursor = 0;
        for (var s = 0; s < spans.length; s++) {
            var span = spans[s];
            var test = src.substring(span.testStart, span.testEnd);
            var trimmed = test.replace(/^\s+|\s+$/g, '');
            var varName = trimmed ? stateVarOf(trimmed) : null;
            var swVar = switchVarAfter(src, span.end);
            var fsmVar = fsmVarAfter(src, span);
            var vars = [];
            if (varName) vars.push(varName);
            if (swVar && vars.indexOf(swVar) === -1) vars.push(swVar);
            if (fsmVar && vars.indexOf(fsmVar) === -1) vars.push(fsmVar);
            for (var v = 0; v < STATE_VARS.length; v++) {
                if (vars.indexOf(STATE_VARS[v]) === -1) vars.push(STATE_VARS[v]);
            }
            var id = 'L' + srcTag + '_' + span.start;
            var varsFn = vars.length
                ? ',function(){return [' + vars.map(function(name) {
                    return '(typeof ' + name + '==="undefined"?null:' + name + ')';
                }).join(',') + '];}'
                : '';
            var condSrc = trimmed ? '(' + test + ')' : 'true';
            // realm 自适应：globalThis 在 window/Worker 都成立；同源 iframe
            // 退回 top；都没有则退化为纯条件求值（不改变被插桩代码语义）。
            var tickCall = '(globalThis.__mcp_vm_loop_tick||(function(){try{' +
                'return globalThis.top.__mcp_vm_loop_tick}catch(e){return null}})()' +
                '||function(i,c){return c();})("' + id +
                '",function(){return ' + condSrc + ';}' + varsFn + ')';
            if (span.kind === 'for') {
                // for(init;TEST;update) → for(init;tick(...);update)
                out += src.substring(cursor, span.start) + 'for(' +
                    span.pre + tickCall + span.post;
            } else {
                out += src.substring(cursor, span.start) + 'while(' + tickCall + ')';
            }
            cursor = span.end;
        }
        out += src.substring(cursor);
        var patched = applyCodePatches(out);
        return { src: patched.src, count: spans.length, patches: patched.applied };
    }
    window.__mcp_vm_loop_inject = injectTicks;

    function recordSource(kind, src, injected, patches) {
        if (sources.length >= MAX_SOURCES) return;
        // 第四阶段 Task 3：≤256KB 的源码一律保留全文（不再以有循环注入为条件），
        // 纯数据/无循环脚本也可离线分析；超限仍只留前缀。
        var keep = src.length <= 262144;
        var entry = {
            kind: kind, length: src.length, hash: hashOf(src),
            loops_injected: injected, ts: Date.now(),
            prefix: src.substring(0, 160),
            source: keep ? src : undefined
        };
        if (patches && patches.length) entry.patches_applied = patches;
        sources.push(entry);
    }

    // ---- 动态代码 hook 1：eval ----
    window.eval = function(src) {
        if (typeof src === 'string') {
            var r = injectTicks(src);
            recordSource('eval', src, r.count, r.patches);
            return _eval.call(this, r.src);
        }
        // TrustedScript 等非字符串参数：BotGuard 的 eval(policy.createScript(code))
        // 通道会经过这里，记录下来以定位 realm
        try { recordSource('eval-nonstr', Object.prototype.toString.call(src), 0); }
        catch (e) {}
        return _eval.call(this, src);
    };
    disguise(window.eval, _eval, 'function eval() { [native code] }');

    // ---- 动态代码 hook 2：Function 族构造器 ----
    // 包一层统一逻辑；window.Function 只挡得住字面引用，
    // (function(){}).constructor / async/generator 构造器都要一并接管。
    function makeHookedFunction(Orig, kind) {
        var Hooked = function() {
            var args = Array.prototype.slice.call(arguments);
            var body = args.length ? String(args[args.length - 1]) : '';
            var r = injectTicks(body);
            recordSource(kind, body, r.count, r.patches);
            args[args.length - 1] = r.src;
            return Orig.apply(null, args);
        };
        Hooked.prototype = Orig.prototype;
        try {
            Object.defineProperty(Hooked, 'name', { value: 'Function' });
        } catch (e) {}
        // 不写自有 toString——自有属性本身即可检测（hasOwnProperty）；
        // 继承守护版 Function.prototype.toString 命中 WeakMap 即可。
        disguise(Hooked, Orig, 'function Function() { [native code] }');
        return Hooked;
    }

    window.Function = makeHookedFunction(_Function, 'Function');
    // (function(){}).constructor 经典绕过：让原型上的 constructor 也指向钩子
    try { _Function.prototype.constructor = window.Function; } catch (e) {}
    // async / generator 构造器（window 上没有，只能从原型链拿）
    // 注意：这些原型上的 constructor 属性 non-writable 但 configurable，
    // 直接赋值会静默失败，必须 defineProperty。
    function hookHiddenConstructor(proto, Orig, kind) {
        try {
            var Hooked = makeHookedFunction(Orig, kind);
            Object.defineProperty(proto, 'constructor',
                { value: Hooked, writable: true, configurable: true });
        } catch (e) {}
    }
    try {
        var asyncProto = Object.getPrototypeOf(async function(){});
        hookHiddenConstructor(asyncProto, asyncProto.constructor, 'AsyncFunction');
    } catch (e) {}
    try {
        var genProto = Object.getPrototypeOf(function*(){});
        hookHiddenConstructor(genProto, genProto.constructor, 'GeneratorFunction');
    } catch (e) {}

    // ---- 动态代码 hook 3：Trusted Types policy ----
    // require-trusted-types-for 'script' 的页面（Google 登录页就是）只能用
    // policy 产出的 TrustedScript 落地动态脚本；包住 createPolicy 即可
    // 透明插桩所有经 policy 的脚本，且不破坏 TT 合规。
    if (typeof window.trustedTypes !== 'undefined' && window.trustedTypes &&
        window.trustedTypes.createPolicy) {
        var _createPolicy = window.trustedTypes.createPolicy.bind(window.trustedTypes);
        window.trustedTypes.createPolicy = function(name, rules) {
            var wrapped = {};
            if (rules && typeof rules.createHTML === 'function')
                wrapped.createHTML = rules.createHTML;
            if (rules && typeof rules.createScriptURL === 'function')
                wrapped.createScriptURL = rules.createScriptURL;
            if (rules && typeof rules.createScript === 'function') {
                wrapped.createScript = function(src) {
                    var r = injectTicks(String(src));
                    recordSource('tt-createScript', String(src), r.count, r.patches);
                    return rules.createScript(r.src);
                };
            } else {
                wrapped.createScript = function(src) {
                    var r = injectTicks(String(src));
                    recordSource('tt-createScript', String(src), r.count, r.patches);
                    return r.src;
                };
            }
            return _createPolicy(name, wrapped);
        };
    }

    // ---- 动态代码 hook 4：script 元素落地（无 TT 环境的兜底路径）----
    // appendChild/insertBefore 前改写 script 文本；TT 页面赋值会抛，已由 hook 3 覆盖。
    // 同时：BotGuard 会建同源 iframe 并在其 realm 里 eval 代码（绕过顶层 hook），
    // 这里在 iframe 挂载时把运行时复制进其 contentWindow。
    function installIntoIframe(node) {
        try {
            if (!node || node.tagName !== 'IFRAME') return;
            var w = node.contentWindow;
            if (!w) return;
            if (!w.__mcp_vm_loop_tick) {
                w.eval(SELF_SRC);  // about:blank 同源 iframe；跨域访问会抛
                recordSource('iframe-install', String(node.src || 'about:blank'), 0);
            }
        } catch (e) {
            try { recordSource('iframe-install-fail', String(e).substring(0, 120), 0); }
            catch (e2) {}
        }
    }
    function rewriteScriptNode(node) {
        try {
            if (!node) return;
            if (node.tagName === 'IFRAME') { installIntoIframe(node); return; }
            if (node.tagName !== 'SCRIPT') return;
            var src = node.textContent;
            if (typeof src !== 'string' || !src) return;
            var r = injectTicks(src);
            recordSource('script-element', src, r.count, r.patches);
            if (r.count && r.src !== src) node.textContent = r.src;
        } catch (e) {}
    }
    var _appendChild = window.Node && window.Node.prototype.appendChild;
    if (_appendChild) {
        window.Node.prototype.appendChild = function(node) {
            rewriteScriptNode(node);
            return _appendChild.call(this, node);
        };
    }
    var _insertBefore = window.Node && window.Node.prototype.insertBefore;
    if (_insertBefore) {
        window.Node.prototype.insertBefore = function(node, ref) {
            rewriteScriptNode(node);
            return _insertBefore.call(this, node, ref);
        };
    }
    // Element 级插入方法（append/replaceWith/insertAdjacentElement 等）
    if (window.Element) {
        ['append', 'prepend', 'before', 'after', 'replaceWith'].forEach(function(m) {
            var orig = window.Element.prototype[m];
            if (typeof orig !== 'function') return;
            window.Element.prototype[m] = function() {
                for (var i = 0; i < arguments.length; i++) rewriteScriptNode(arguments[i]);
                return orig.apply(this, arguments);
            };
        });
        var _iae = window.Element.prototype.insertAdjacentElement;
        if (_iae) {
            window.Element.prototype.insertAdjacentElement = function(pos, node) {
                rewriteScriptNode(node);
                return _iae.call(this, pos, node);
            };
        }
    }
    // 观察 iframe 创建本身（contentWindow 在挂载后才可用，挂载钩子里再装运行时）
    if (window.Document && window.Document.prototype.createElement) {
        var _createElement = window.Document.prototype.createElement;
        window.Document.prototype.createElement = function(tag) {
            var el = _createElement.apply(this, arguments);
            try {
                if (String(tag).toLowerCase() === 'iframe') {
                    recordSource('iframe-create', '', 0);
                    el.addEventListener('load', function() {
                        installIntoIframe(el);
                    });
                }
            } catch (e) {}
            return el;
        };
    }

    // ---- 动态代码 hook 5：Worker 全量插桩（第四阶段 Task 1）----
    // 链路：Blob(parts) 同步缓存文本 → createObjectURL 建 url→blob 映射 →
    // new Worker(url) 时同步取回源码、injectTicks 插桩、前置 globalThis 版
    // 运行时，重打包为新 blob URL 交给真 Worker。拿不到源码（跨域/模块/
    // data:）不伪造覆盖，记 worker-gap。嵌套 Worker 由运行时递归覆盖。
    var blobText = typeof WeakMap !== 'undefined' ? new WeakMap() : null;
    var _Blob = window.Blob;
    if (blobText && typeof _Blob !== 'undefined') {
        window.Blob = function(parts, opts) {
            var b = new _Blob(parts, opts);
            try {
                if (Object.prototype.toString.call(parts) === '[object Array]' &&
                    parts.every(function(p) { return typeof p === 'string'; }))
                    blobText.set(b, parts.join(''));
            } catch (e) {}
            return b;
        };
        window.Blob.prototype = _Blob.prototype;
        disguise(window.Blob, _Blob, 'function Blob() { [native code] }');
    }
    var urlBlob = {};
    var _createObjectURL = (window.URL && window.URL.createObjectURL) ?
        window.URL.createObjectURL.bind(window.URL) : null;
    if (_createObjectURL) {
        window.URL.createObjectURL = function(obj) {
            var u = _createObjectURL(obj);
            try { if (blobText && blobText.has(obj)) urlBlob[u] = obj; } catch (e) {}
            return u;
        };
        disguise(window.URL.createObjectURL, _createObjectURL,
                 'function createObjectURL() { [native code] }');
    }

    var workers = [];   // 活 Worker 注册表（drain 聚合用）
    var drainSeq = 0;

    function rewriteWorkerSource(src, label) {
        var r = injectTicks(src);
        recordSource('worker-' + label, src, r.count, r.patches);
        return SELF_SRC + '\n' + r.src;
    }
    function instrumentWorkerUrl(url) {
        var b = urlBlob[url];
        if (b && blobText && blobText.has(b))
            return rewriteWorkerSource(blobText.get(b), 'blob');
        if (typeof url === 'string' && /^data:/i.test(url)) {
            // data: URL 自带全部源码（第五阶段，抖音 SDK 嵌套 Worker 实测
            // 缺口）：解码后同样插桩，不再落 worker-gap。
            try {
                var comma = url.indexOf(',');
                var meta = url.substring(0, comma);
                var data = url.substring(comma + 1);
                var text = /;base64/i.test(meta)
                    ? decodeURIComponent(escape(window.atob(data)))
                    : decodeURIComponent(data);
                return rewriteWorkerSource(text, 'data');
            } catch (e) {}
            return null;
        }
        if (typeof url === 'string' && !/^(blob|data):/i.test(url) &&
            typeof window.XMLHttpRequest !== 'undefined') {
            // 同源普通 URL：同步 XHR 取源码（deprecated 但主世界可用）
            try {
                var xhr = new window.XMLHttpRequest();
                xhr.open('GET', url, false);
                xhr.send();
                if (xhr.status === 0 || (xhr.status >= 200 && xhr.status < 300))
                    return rewriteWorkerSource(String(xhr.responseText), 'url');
            } catch (e) {}
        }
        return null;
    }

    if (typeof window.Worker !== 'undefined') {
        var _Worker = window.Worker;
        window.Worker = function(url, opts) {
            var src = null;
            try { src = instrumentWorkerUrl(String(url)); } catch (e) {}
            if (src === null || !_Blob || !_createObjectURL) {
                if (src === null)
                    recordSource('worker-gap', String(url), -1);
                // 落 gap 的直通 Worker 也入注册表（第十一阶段）：route 层
                // 已对该 URL 的 worker 响应前置运行时（运行时自带 worker 侧
                // message 应答），drain 桥可跨 realm 问到它；无运行时的直通
                // Worker 会 drain 超时，如实记超时条目。
                // 构造抛错（sandbox null-principal 下 Firefox 直接
                // SecurityError，worker 根本没执行）记 worker-blocked，
                // 与「执行了但没插桩」区分。
                try {
                    var pw = new _Worker(url, opts);
                    try { workers.push(pw); } catch (e) {}
                    return pw;
                } catch (cerr) {
                    try {
                        recordSource('worker-blocked',
                            String(url) + ' :: ' + String(cerr).substring(0, 120), -1);
                    } catch (e) {}
                    throw cerr;
                }
            }
            var w = new _Worker(_createObjectURL(new _Blob([src],
                { type: 'text/javascript' })), opts);
            workers.push(w);
            return w;
        };
        window.Worker.prototype = _Worker.prototype;
        disguise(window.Worker, _Worker, 'function Worker() { [native code] }');
    }

    // ---- 动态代码 hook 7：Worker 内 importScripts（第五阶段）----
    // reCAPTCHA 实测缺口：Worker 入口文件只是 loader，主力代码经
    // importScripts(url) 落地。Worker 内允许同步 XHR，取回源码、插桩、
    // 重打包为 blob URL 再交给真 importScripts（blob: 在 Worker 内合法，
    // 语义与直接 importScripts 等价）。取不到记 importscripts-gap。
    if (typeof window.document === 'undefined' &&
        typeof window.importScripts === 'function' &&
        _Blob && _createObjectURL) {
        var _importScripts = window.importScripts.bind(window);
        window.importScripts = function() {
            var urls = Array.prototype.slice.call(arguments);
            var out = [];
            for (var i = 0; i < urls.length; i++) {
                var u = String(urls[i]);
                var src = null;
                try {
                    var xhr = new window.XMLHttpRequest();
                    xhr.open('GET', u, false);  // Worker 内同步 XHR 合法
                    xhr.send();
                    if (xhr.status === 0 || (xhr.status >= 200 && xhr.status < 300))
                        src = String(xhr.responseText);
                } catch (e) {}
                if (src === null) {
                    try { recordSource('importscripts-gap', u, -1); } catch (e) {}
                    out.push(u);
                    continue;
                }
                var r = injectTicks(src);
                recordSource('importscripts', src, r.count, r.patches);
                out.push(_createObjectURL(new _Blob([r.src],
                    { type: 'text/javascript' })));
            }
            return _importScripts.apply(null, out);
        };
        disguise(window.importScripts, _importScripts,
                 'function importScripts() { [native code] }');
    }

    // Worker realm 内的 drain 桥：无 document 的环境即 Worker/ServiceWorker 作用域，
    // 注册消息监听，收到 drain 请求后回传 drainAllAsync 结果（含嵌套 Worker）。
    if (typeof window.document === 'undefined' &&
        typeof window.addEventListener === 'function' &&
        typeof window.postMessage === 'function') {
        window.addEventListener('message', function(e) {
            var d = e && e.data;
            if (!d || !d.__mcp_vm_drain) return;
            window.__mcp_vm_loop_state.drainAllAsync(600).then(function(all) {
                window.postMessage({ __mcp_vm_drain_reply: d.__mcp_vm_drain,
                                     data: all });
            });
        });
    }

    // 跨域 iframe drain 桥·应答侧（第十阶段切片 D）：init script 覆盖所有
    // frame（含跨域），但父页面无法同步访问跨域 frame 的 realm 状态。
    // 每个 window realm 监听 drain 请求：仅响应来自父窗口（e.source ===
    // window.parent）的请求，回传本 realm 的 drainAllAsync 结果（含本
    // frame 的 Worker 与嵌套 frame，递归聚合）。e.source 校验避免页面
    // 自身伪造请求套取轨迹数据。
    if (typeof window.document !== 'undefined' &&
        typeof window.addEventListener === 'function') {
        window.addEventListener('message', function(e) {
            var d = e && e.data;
            if (!d || !d.__mcp_vm_drain_req) return;
            var respondTo = e.source;
            if (!respondTo || (window.parent && respondTo !== window.parent))
                return;
            window.__mcp_vm_loop_state.drainAllAsync(600).then(function(all) {
                try {
                    respondTo.postMessage(
                        { __mcp_vm_drain_res: d.__mcp_vm_drain_req,
                          data: all }, '*');
                } catch (err) {}
            });
        });
    }

    // ServiceWorker 盲区登记（第十阶段切片 D）：init script 覆盖不到
    // ServiceWorker realm（独立进程/生命周期，注册脚本由浏览器直接拉取
    // 执行），无法插桩；包装 register 如实记 serviceworker-gap 事件，
    // 让评分卡能把「目标用了 SW」与「浏览器漏记」区分开。
    try {
        var SWC = window.ServiceWorkerContainer;
        if (SWC && SWC.prototype &&
            typeof SWC.prototype.register === 'function') {
            var _swRegister = SWC.prototype.register;
            SWC.prototype.register = function(scriptURL, opts) {
                try {
                    recordSource('serviceworker-gap', String(scriptURL), -1);
                } catch (e) {}
                return _swRegister.call(this, scriptURL, opts);
            };
        }
    } catch (e) {}

    // ---- 动态代码 hook 8：WebAssembly 全入口插桩（第六阶段）----
    // 抖音 pylon-wasm 实测盲区：wasm 模块字节、imports/exports、
    // wasm↔JS 边界调用全部不可见。这里对 compile/instantiate/
    // compileStreaming/instantiateStreaming 四入口插桩：
    //   - 模块字节：哈希（31 滚动，与 hashOf 同款便于 Python 交叉）+
    //     ≤768KB 内嵌 base64 全文；超限留哈希靠 raw 网络捕获交叉命中；
    //   - WeakMap 追 module→字节（instantiate(module) 形态回找 compile
    //     时留存 的字节）；
    //   - imports/exports 函数包装计数，入 __mcp_vm_counters.wasm_imports /
    //     wasm_exports（键 <hash>:<name>），随 drain 跨 realm 合并；
    //   - 语义保持：instantiate(bytes) 返回 {module,instance}、
    //     instantiate(module) 返回 Instance，两种形态原样透传。
    var wasmModules = [];
    var wasmBytesOf = typeof WeakMap !== 'undefined' ? new WeakMap() : null;
    var WASM_B64_CAP = 786432;  // 768KB，超出只留哈希

    function hashBytes(bytes) {  // 字节版 31 滚动哈希（hashOf 同款）
        var h = 0;
        for (var i = 0; i < bytes.length; i++) h = (h * 31 + bytes[i]) | 0;
        return (h >>> 0).toString(16);
    }

    // 捕获原始 fromCharCode 供 bytesToB64 使用：hook 10 包装后
    // 若仍走公共入口，每次 wasm 字节转码都会自产值事件（自污染）。
    var _fromCharCode0 = window.String && window.String.fromCharCode;

    function bytesToB64(bytes) {
        if (typeof window.btoa !== 'function' || !_fromCharCode0)
            return undefined;
        var parts = [];
        for (var i = 0; i < bytes.length; i += 32768) {
            parts.push(_fromCharCode0.apply(null,
                bytes.subarray(i, Math.min(i + 32768, bytes.length))));
        }
        try { return window.btoa(parts.join('')); } catch (e) { return undefined; }
    }

    function wasmCounter(kind) {
        var c = globalThis.__mcp_vm_counters;
        if (!c) c = globalThis.__mcp_vm_counters = {};
        if (!c[kind]) c[kind] = {};
        return c[kind];
    }

    // 计数键前缀惰性求值：streaming 形态哈希异步才出，先以临时
    // provId 包装 imports/exports，字节到达后翻转 idBox.id 并迁移
    // 已积累的计数键，最终键统一为 <hash>:<name>。
    function wrapWasmImports(imports, idBox) {
        if (!imports || typeof imports !== 'object') return imports;
        var counts = wasmCounter('wasm_imports');
        var wrapped = {};
        for (var modName in imports) {
            if (!Object.prototype.hasOwnProperty.call(imports, modName)) continue;
            var mod = imports[modName];
            if (!mod || typeof mod !== 'object') { wrapped[modName] = mod; continue; }
            var wm = {};
            for (var fname in mod) {
                if (!Object.prototype.hasOwnProperty.call(mod, fname)) continue;
                var fn = mod[fname];
                if (typeof fn !== 'function') { wm[fname] = fn; continue; }
                (function(f, qual) {
                    wm[fname] = function() {
                        var key = idBox.id + ':' + qual;
                        counts[key] = (counts[key] | 0) + 1;
                        return f.apply(this, arguments);
                    };
                })(fn, modName + '.' + fname);
            }
            wrapped[modName] = wm;
        }
        return wrapped;
    }

    function wrapWasmExports(instance, idBox) {
        // 返回 {names, frozen, shadowed}。SpiderMonkey 冻结 exports 对象
        // 且 exports 是原型 getter（第十阶段切片 C1 实测：直接赋值静默
        // 失败；Proxy get 对冻结的非 writable/non-configurable 函数属性
        // 违反不变量抛 TypeError）。可行方案：浅拷贝包装对象 +
        // defineProperty 在 instance 上遮蔽原型 getter——冻结与否都能
        // 计数。shadowed=false 表示遮蔽失败，此时计数不可用如实记录。
        try {
            var exp = instance && instance.exports;
            if (!exp) return null;
            var names = [];
            var frozen = Object.isFrozen ? !!Object.isFrozen(exp) : false;
            var counts = wasmCounter('wasm_exports');
            var wrapped = {};
            for (var name in exp) {
                if (!Object.prototype.hasOwnProperty.call(exp, name)) continue;
                names.push(name);
                var val = exp[name];
                if (typeof val !== 'function') { wrapped[name] = val; continue; }
                (function(f, qual) {
                    wrapped[qual] = function() {
                        var key = idBox.id + ':' + qual;
                        counts[key] = (counts[key] | 0) + 1;
                        return f.apply(this, arguments);
                    };
                })(val, name);
            }
            var shadowed = false;
            try {
                Object.defineProperty(instance, 'exports', {
                    value: wrapped, writable: true,
                    enumerable: true, configurable: true });
                shadowed = instance.exports === wrapped;
            } catch (e) {}
            return { names: names, frozen: frozen, shadowed: shadowed };
        } catch (e) { return null; }
    }

    function migrateWasmKeys(fromId, toHash) {
        ['wasm_imports', 'wasm_exports'].forEach(function(kind) {
            var c = wasmCounter(kind);
            for (var k in c) {
                if (!Object.prototype.hasOwnProperty.call(c, k)) continue;
                if (k.indexOf(fromId + ':') !== 0) continue;
                var nk = toHash + ':' + k.slice(fromId.length + 1);
                c[nk] = (c[nk] | 0) + c[k];
                delete c[k];
            }
        });
    }

    function recordWasmModule(bytes, kind) {
        var hash = hashBytes(bytes);
        var entry = { hash: hash, bytes_len: bytes.length, kind: kind,
                      ts: Date.now() };
        if (bytes.length <= WASM_B64_CAP) entry.bytes_b64 = bytesToB64(bytes);
        wasmModules.push(entry);
        return entry;
    }

    function findWasmEntry(hash) {
        for (var i = wasmModules.length - 1; i >= 0; i--) {
            if (wasmModules[i].hash === hash) return wasmModules[i];
        }
        return null;
    }

    var wasmSeq = 0;

    if (typeof window.WebAssembly !== 'undefined') {
        var WA = window.WebAssembly;
        if (typeof WA.compile === 'function') {
            var _wcompile = WA.compile.bind(WA);
            WA.compile = function(bytes) {
                var u8 = new Uint8Array(bytes);
                var entry = recordWasmModule(u8, 'compile');
                return _wcompile(bytes).then(function(m) {
                    // 存 {bytes, entry}：instantiate(module) 形态精确回写
                    // compile 条目，不靠「同哈希最新条目」猜测
                    try { if (wasmBytesOf) wasmBytesOf.set(m, {
                        bytes: u8, entry: entry }); } catch (e) {}
                    entry.module_compiled = true;
                    return m;
                });
            };
        }
        if (typeof WA.instantiate === 'function') {
            var _winstantiate = WA.instantiate.bind(WA);
            WA.instantiate = function(source, imports) {
                var u8 = null, hash = null, targetEntry = null;
                if (source instanceof WA.Module) {
                    var rec = (wasmBytesOf && wasmBytesOf.has(source))
                        ? wasmBytesOf.get(source) : null;
                    if (rec) {
                        u8 = rec.bytes;
                        targetEntry = rec.entry || null;
                    }
                    if (u8) hash = hashBytes(u8);
                } else {
                    u8 = new Uint8Array(source);
                    targetEntry = recordWasmModule(u8, 'instantiate');
                    hash = targetEntry.hash;
                }
                var idBox = { id: hash || 'unknown' };
                var wrappedImports = hash ? wrapWasmImports(imports, idBox)
                                          : imports;
                return _winstantiate(source, wrappedImports).then(function(res) {
                    // bytes 形态返回 {module, instance}；module 形态返回 Instance
                    var inst = (res && res.instance) ? res.instance : res;
                    if (hash && inst) {
                        var r = wrapWasmExports(inst, idBox);
                        var entry = targetEntry || findWasmEntry(hash);
                        if (entry && r) {
                            entry.exports = r.names;
                            if (r.frozen) entry.exports_frozen = true;
                            if (r.shadowed === false)
                                entry.exports_shadow_failed = true;
                        }
                    }
                    return res;
                });
            };
        }
        // streaming 变体：旁路 clone 响应取字节，不消耗原流
        if (typeof WA.compileStreaming === 'function') {
            var _wcs = WA.compileStreaming.bind(WA);
            WA.compileStreaming = function(source) {
                try {
                    Promise.resolve(source).then(function(resp) {
                        try {
                            resp.clone().arrayBuffer().then(function(buf) {
                                recordWasmModule(new Uint8Array(buf),
                                                 'compileStreaming');
                            });
                        } catch (e) {}
                    });
                } catch (e) {}
                return _wcs(source);
            };
        }
        if (typeof WA.instantiateStreaming === 'function') {
            var _wis = WA.instantiateStreaming.bind(WA);
            WA.instantiateStreaming = function(source, imports) {
                var provId = 's' + (++wasmSeq);
                var idBox = { id: provId };
                var entryBox = { entry: null, promise: null };
                try {
                    source = Promise.resolve(source).then(function(resp) {
                        var cloned = resp.clone();
                        entryBox.promise = cloned.arrayBuffer().then(
                            function(buf) {
                                var e = recordWasmModule(
                                    new Uint8Array(buf),
                                    'instantiateStreaming');
                                e.prov_id = provId;
                                entryBox.entry = e;
                                migrateWasmKeys(provId, e.hash);
                                idBox.id = e.hash;
                            });
                        return resp;
                    });
                } catch (e) {}
                // imports 必须在实例化前包装，此时哈希未出，用 provId
                var wrappedImports = wrapWasmImports(imports, idBox);
                return _wis(source, wrappedImports).then(function(res) {
                    var finish = function() {
                        try {
                            if (res && res.instance) {
                                var r = wrapWasmExports(res.instance, idBox);
                                if (entryBox.entry && r) {
                                    entryBox.entry.exports = r.names;
                                    if (r.frozen)
                                        entryBox.entry.exports_frozen = true;
                                    if (r.shadowed === false)
                                        entryBox.entry
                                            .exports_shadow_failed = true;
                                }
                            }
                        } catch (e2) {}
                        return res;
                    };
                    // 竞态修复：等旁路字节落账再回写 exports；
                    // 旁路失败不阻塞页面语义（取值原样透传）
                    return Promise.resolve(entryBox.promise)
                        .then(finish, finish);
                });
            };
        }
        // 构造器直连盲区（第十二阶段 Task 1）：new WebAssembly.Instance
        // (module, imports) 不经过上面四个入口，实例与 exports 调用全部
        // 不可见。包装构造器：字节经 wasmBytesOf 回找 compile 时留存的
        // 记录；模块来路不明（页面直接 new WebAssembly.Module——构造器
        // 尚未插桩——或 hook 安装前已编译）如实记 bytes_unavailable，
        // 不伪造字节、不包装 exports（与 instantiate(module) 无字节记录
        // 路径一致）。exports 计数沿用浅拷贝 + defineProperty 遮蔽方案，
        // SpiderMonkey 冻结 exports + 原型 getter 形态同样可计数。
        // 语义保持：返回值是真 Instance（构造器返回对象覆盖 new 的
        // this），Hooked.prototype 指向原生 prototype（instanceof 与
        // WebAssembly.Instance.prototype 均不变），toString 经 fakeFns
        // 守护登记。
        if (typeof WA.Instance === 'function') {
            var _wInstance = WA.Instance;
            var HookedInstance = function(source, imports) {
                var u8 = null, hash = null;
                try {
                    if (source instanceof WA.Module) {
                        var rec = (wasmBytesOf && wasmBytesOf.has(source))
                            ? wasmBytesOf.get(source) : null;
                        if (rec) u8 = rec.bytes;
                    }
                    // 非 Module 输入：真引擎抛 TypeError，交由下面原构造器
                    // 原样抛出——这里不提前读字节，避免误记空字节条目。
                    if (u8) hash = hashBytes(u8);
                } catch (e) {}
                var idBox = { id: hash || 'unknown' };
                var wrappedImports = hash ? wrapWasmImports(imports, idBox)
                                          : imports;
                var inst = new _wInstance(source, wrappedImports);  // 异常原样抛
                try {
                    var entry;
                    if (u8) {
                        // 字节已在任一先前列目留存过（compile 等带
                        // bytes_b64 的条目）：只记哈希/长度交叉引用
                        // （bytes_ref），不重复内嵌 base64
                        entry = { hash: hash, bytes_len: u8.length,
                                  kind: 'Instance-ctor', ts: Date.now() };
                        var hasB64 = false;
                        for (var wi = wasmModules.length - 1; wi >= 0; wi--) {
                            if (wasmModules[wi].hash === hash &&
                                wasmModules[wi].bytes_b64 !== undefined) {
                                hasB64 = true;
                                break;
                            }
                        }
                        if (hasB64) entry.bytes_ref = true;
                        else if (u8.length <= WASM_B64_CAP)
                            entry.bytes_b64 = bytesToB64(u8);
                        wasmModules.push(entry);
                        var r = wrapWasmExports(inst, idBox);
                        if (r) {
                            entry.exports = r.names;
                            if (r.frozen) entry.exports_frozen = true;
                            if (r.shadowed === false)
                                entry.exports_shadow_failed = true;
                        }
                    } else {
                        // 诚实登记 gap：module 来路不明，字节不可得，
                        // exports 计数也不可用（无哈希键）
                        wasmModules.push({ hash: null, bytes_len: null,
                            kind: 'Instance-ctor',
                            bytes_unavailable: true, ts: Date.now() });
                    }
                } catch (e) {}
                return inst;
            };
            HookedInstance.prototype = _wInstance.prototype;
            WA.Instance = HookedInstance;
            disguise(HookedInstance, _wInstance,
                     'function Instance() { [native code] }');
        }
    }

    // ---- 动态代码 hook 9：值变换事件（第七阶段 taint-lite）----
    // 加密参数链路「变换」层的编码/加密 API 取证：记录输入/输出的
    // 哈希（hashOf/hashBytes 同款，Python 侧可复算）、长度与 ≤64 字符
    // 预览，不全文留存。离线关联器把请求字段值匹配回产生它的 API 调用。
    // 环形缓冲 1000 条/realm；包装不改变返回值与异常语义。
    var valueTaps = [];
    var valueTapOverflow = 0;
    var VALUE_TAP_CAP = 1000;
    var PREVIEW_LEN = 64;

    function strMeta(s) {
        s = String(s);
        return { len: s.length, hash: hashOf(s),
                 preview: s.length <= PREVIEW_LEN
                     ? s : s.substring(0, PREVIEW_LEN) };
    }

    function bytesMeta(b) {  // hex 预览前 32 字节（64 字符）
        var u8 = (typeof Uint8Array !== 'undefined' &&
                  b instanceof Uint8Array) ? b : new Uint8Array(b);
        var prev = '';
        for (var i = 0; i < Math.min(u8.length, 32); i++)
            prev += ('0' + u8[i].toString(16)).slice(-2);
        return { len: u8.length, hash: hashBytes(u8), preview: prev };
    }

    function metaOf(v) {
        if (typeof v === 'string') return strMeta(v);
        if (v && typeof Uint8Array !== 'undefined' &&
            (v instanceof Uint8Array ||
             (typeof ArrayBuffer !== 'undefined' &&
              v instanceof ArrayBuffer)))
            return bytesMeta(v);
        return { len: 0, hash: null,
                 preview: String(v).substring(0, PREVIEW_LEN) };
    }

    function pushValueTap(ev) {
        if (valueTaps.length >= VALUE_TAP_CAP) {
            valueTaps.shift();
            valueTapOverflow++;
        }
        valueTaps.push(ev);
    }

    function tapValue(api, input, output, extra) {
        var ev = { api: api, ts: Date.now() };
        try {
            var mi = metaOf(input);
            ev.in_len = mi.len; ev.in_hash = mi.hash; ev.in_preview = mi.preview;
            var mo = metaOf(output);
            ev.out_len = mo.len; ev.out_hash = mo.hash;
            ev.out_preview = mo.preview;
            // 附加字段（第十二阶段：coalesced 事件的起止索引等）
            if (extra) {
                for (var ek in extra) {
                    if (Object.prototype.hasOwnProperty.call(extra, ek))
                        ev[ek] = extra[ek];
                }
            }
        } catch (e) {}
        pushValueTap(ev);
    }

    // 单参字符串变换：btoa/atob/encodeURIComponent/decodeURIComponent
    ['btoa', 'atob', 'encodeURIComponent', 'decodeURIComponent']
        .forEach(function(name) {
            var orig = window[name];
            if (typeof orig !== 'function') return;
            window[name] = function(x) {
                var out = orig.apply(this, arguments);  // 异常原样抛出
                try { tapValue(name, x, out); } catch (e) {}
                return out;
            };
        });

    if (typeof window.TextEncoder !== 'undefined') {
        var _teEncode = window.TextEncoder.prototype.encode;
        window.TextEncoder.prototype.encode = function(str) {
            var out = _teEncode.apply(this, arguments);
            try { tapValue('TextEncoder.encode', str, out); } catch (e) {}
            return out;
        };
    }
    if (typeof window.TextDecoder !== 'undefined') {
        var _tdDecode = window.TextDecoder.prototype.decode;
        window.TextDecoder.prototype.decode = function(buf) {
            var out = _tdDecode.apply(this, arguments);
            try { tapValue('TextDecoder.decode', buf, out); } catch (e) {}
            return out;
        };
    }

    // crypto.subtle 异步：Promise 完成后记事件（ts 取完成时刻），
    // 最后一个实参视为数据（digest 2 参、encrypt/sign 3 参）
    var _subtle = window.crypto && window.crypto.subtle;
    if (_subtle) {
        ['digest', 'encrypt', 'sign'].forEach(function(name) {
            var orig = _subtle[name];
            if (typeof orig !== 'function') return;
            _subtle[name] = function() {
                var data = arguments[arguments.length - 1];
                var p = orig.apply(this, arguments);
                return p.then(function(out) {
                    try {
                        tapValue('crypto.subtle.' + name, data, out);
                    } catch (e) {}
                    return out;
                });
            };
        });
    }

    // ---- 动态代码 hook 10：字符串装配原语（第八阶段）----
    // String.fromCharCode 是混淆 VM 拼装签名/参数的典型末段。
    // 单字符调用是 VM 热噪声（331k 迭代量级）：第十阶段切片 C2 起改为
    // 追加进合并缓冲 sfcPending——触发 ≥2 字符调用 / 缓冲满 64 字符 /
    // drain 前 flush 为一条 String.fromCharCode(coalesced) 事件。
    // 331k 单字符调用从「只有计数」变成 ~5k 条可片段匹配的装配事件，
    // 体积可控。限制：并行装配的多条链会并入同一缓冲（无法拆链），
    // 但 64 字符粒度下片段匹配（check 6 形态）仍可用。
    // 计数走独立字段而非 __mcp_vm_counters：补丁的自初始化惯用法
    // （obj=obj||{key:{}}）假定整对象不存在，预建会破坏补丁。
    var sfcCalls = 0;
    var sfcCoalesced = 0;
    var sfcPending = '';
    var SFC_COALESCE_MAX = 64;
    function sfcFlush() {
        if (!sfcPending) return;
        var merged = sfcPending;
        sfcPending = '';
        sfcCoalesced++;
        try {
            tapValue('String.fromCharCode(coalesced)',
                     merged.length + ' chars', merged);
        } catch (e) {}
    }
    var _sfc = window.String && window.String.fromCharCode;
    if (typeof _sfc === 'function') {
        window.String.fromCharCode = function() {
            var out = _sfc.apply(null, arguments);
            sfcCalls++;
            if (out.length === 1) {
                sfcPending += out;
                if (sfcPending.length >= SFC_COALESCE_MAX) sfcFlush();
            } else if (out.length >= 2) {
                sfcFlush();
                try {
                    tapValue('String.fromCharCode',
                             arguments.length + ' args', out);
                } catch (e) {}
            }
            return out;
        };
    }

    // ---- 动态代码 hook 11：逐字符读取原语（第十二阶段 Task 2）----
    // VM 里 s.charCodeAt(i) 逐字符取码是字符串装配的「读取」侧中间态，
    // 此前不可见（登记边界）。逐 call 记事件是热路径噪声（与单字符
    // fromCharCode 同量级），照搬合并缓冲设计：同一字符串、同一方法、
    // 索引严格递增（idx === last+1）的连续读取合并为一条 coalesced
    // 事件——带源字符串哈希、起止索引、码元序列预览；缓冲满 64 码元
    // flush，drain 残余落账。返回值/异常原样透传；每 call 只做计数 +
    // 标量比较 + 数组追加，不分配大对象。hashOf 走 hook 前捕获的
    // _charCodeAt0，不自产事件。如实限制：多链交替读取（A、B 串交错）
    // 会互相截断成短事件（单缓冲，与 fromCharCode 同款）；at() 负索引
    // 倒读不合并（索引递减即断链）；codePointAt 超 0xFFFF 的码元在
    // 无 fromCodePoint 的环境里预览位记 '?'。
    var spCalls = { charCodeAt: 0, at: 0, codePointAt: 0 };
    var spCoalesced = 0;
    var spStr = null, spMethod = '', spStart = 0, spLast = -1;
    var spCodes = [];
    var spHashCache = { str: null, len: 0, hash: null, preview: '' };
    var SP_COALESCE_MAX = 64;

    function spFlush() {
        if (!spCodes.length) return;
        var str = spStr, method = spMethod, start = spStart, last = spLast;
        var codes = spCodes;
        spCodes = []; spStr = null; spMethod = '';
        spCoalesced++;
        try {
            var seq = '';
            for (var i = 0; i < codes.length; i++) {
                var c = codes[i];
                if (typeof c === 'number') {
                    seq += (c > 0xFFFF)
                        ? (window.String.fromCodePoint
                            ? window.String.fromCodePoint(c) : '?')
                        : (_fromCharCode0 ? _fromCharCode0(c) : '?');
                } else {
                    seq += String(c);  // at() 返回单字符字符串
                }
            }
            // 源字符串元数据单条缓存：线性扫长串时每 64 码元一条事件，
            // 避免每条事件重哈希整串（O(n²/64) 退化）
            var mi;
            if (spHashCache.str === str) {
                mi = spHashCache;
            } else {
                var sm = strMeta(str);
                mi = spHashCache = { str: str, len: sm.len,
                                     hash: sm.hash, preview: sm.preview };
            }
            var ev = { api: 'String.' + method + '(coalesced)',
                       ts: Date.now(),
                       in_len: mi.len, in_hash: mi.hash,
                       in_preview: mi.preview,
                       out_len: codes.length, out_hash: hashOf(seq),
                       out_preview: seq.length <= PREVIEW_LEN
                           ? seq : seq.substring(0, PREVIEW_LEN),
                       start_index: start, end_index: last };
            pushValueTap(ev);
        } catch (e) {}
    }

    function wrapStringPrim(method, orig) {
        var wrapped = function() {
            // 严格模式：this 不做装箱/全局替换，charCodeAt.call(null)
            // 这类非法 receiver 由原生实现原样抛 TypeError（语义零改变），
            // 正常调用时 this 保持原始字符串，String(this) 也更便宜
            "use strict";
            var out = orig.apply(this, arguments);  // 返回值/异常原样
            spCalls[method]++;
            try {
                var s = String(this);
                var idx = arguments.length ? +arguments[0] : 0;
                if (idx !== idx) idx = 0;  // NaN 归 0（引擎同语义）
                // 越界：charCodeAt→NaN、at/codePointAt→undefined，
                // 断链且不入序列（调用仍计数）
                var valid = (out === out) && out !== undefined;
                if (valid && spCodes.length && spStr === s &&
                    spMethod === method && idx === spLast + 1) {
                    spCodes.push(out);
                    spLast = idx;
                } else {
                    spFlush();
                    if (valid) {
                        spStr = s; spMethod = method;
                        spStart = idx; spLast = idx;
                        spCodes.push(out);
                    }
                }
                if (spCodes.length >= SP_COALESCE_MAX) spFlush();
            } catch (e) {}
            return out;
        };
        disguise(wrapped, orig);  // name/length 对齐 + fakeFns 守护登记
        return wrapped;
    }
    var _spProto = window.String && window.String.prototype;
    if (_spProto) {
        if (typeof _spProto.charCodeAt === 'function')
            _spProto.charCodeAt = wrapStringPrim('charCodeAt',
                                                 _spProto.charCodeAt);
        if (typeof _spProto.at === 'function')
            _spProto.at = wrapStringPrim('at', _spProto.at);
        if (typeof _spProto.codePointAt === 'function')
            _spProto.codePointAt = wrapStringPrim('codePointAt',
                                                  _spProto.codePointAt);
    }

    // ---- 序列记录设施（第九阶段）：__mcp_vm_rec(tag, vals) ----
    // 代码补丁（目标适配器锚点）在 handler 入口插入调用，产物即含
    // 有序触发序列 + 参数证据（metaOf：哈希+长度+≤64 预览）。
    // 环形 5000 条/realm；热路径开销最小化。返回值记录需包裹函数体，
    // 正则锚不通用——本设施记入口序列与参数，返回值由适配器锚 return。
    var traceSeq = [];
    var traceSeqOverflow = 0;
    var TRACE_SEQ_CAP = 5000;
    var traceSeqNo = 0;

    function traceMetaList(vals) {
        var arr = [];
        for (var i = 0; i < vals.length && i < 8; i++) {
            try {
                var m = metaOf(vals[i]);
                arr.push({ len: m.len, hash: m.hash,
                           preview: m.preview });
            } catch (e) { arr.push(null); }
        }
        return arr;
    }
    function pushTrace(ev) {
        if (traceSeq.length >= TRACE_SEQ_CAP) {
            traceSeq.shift();
            traceSeqOverflow++;
        }
        traceSeq.push(ev);
    }

    window.__mcp_vm_rec = function(tag, vals) {
        var ev = { seq: ++traceSeqNo, tag: String(tag), ts: Date.now() };
        if (vals !== undefined) ev.vals = traceMetaList(vals);
        pushTrace(ev);
    };

    // 返回值锚定通用设施（第十二阶段 Task 3）：__mcp_vm_rec(tag, vals)
    // 只记入口序列与参数；代码补丁 replacement 里
    //   return __mcp_vm_rec_ret(tag, origFn, this, arguments)
    // 一行记「调用 + 返回值」。ret 与 vals 元素同形（len/hash/preview）；
    // 异常原样抛出，事件记 threw + error_preview（≤64 字符）；args 为
    // null/undefined 时不记 vals。fn 非函数时 apply 抛 TypeError 同样
    // 原样透出（视为适配器误用，不吞错）。
    window.__mcp_vm_rec_ret = function(tag, fn, thisArg, args) {
        var ev = { seq: ++traceSeqNo, tag: String(tag), ts: Date.now() };
        if (args !== undefined && args !== null) ev.vals = traceMetaList(args);
        try {
            var ret = fn.apply(thisArg, args);
        } catch (e) {
            ev.threw = true;
            try {
                ev.error_preview = String(e && e.message || e)
                    .substring(0, PREVIEW_LEN);
            } catch (e2) {}
            pushTrace(ev);
            throw e;
        }
        try {
            var m = metaOf(ret);
            ev.ret = { len: m.len, hash: m.hash, preview: m.preview };
        } catch (e) {}
        pushTrace(ev);
        return ret;
    };

    // ---- 动态代码 hook 6：setTimeout/setInterval 字符串参数（只记录）----
    ['setTimeout', 'setInterval'].forEach(function(name) {
        var orig = window[name];
        if (typeof orig !== 'function') return;
        window[name] = function(handler) {
            if (typeof handler === 'string') {
                var r = injectTicks(handler);
                recordSource(name, handler, r.count, r.patches);
                var args = Array.prototype.slice.call(arguments);
                args[0] = r.src;
                return orig.apply(this, args);
            }
            return orig.apply(this, arguments);
        };
        disguise(window[name], orig,
                 'function ' + name + '() { [native code] }');
    });

    function safeStringify(obj) {
        var seen = [];
        return JSON.stringify(obj, function(key, val) {
            if (typeof val === 'bigint') return String(val);
            if (typeof val === 'function') return '[Function]';
            if (typeof val === 'string' && val.length > 300000)
                return val.substring(0, 300000) + '...[truncated]';
            if (typeof val === 'object' && val !== null) {
                if (seen.indexOf(val) !== -1) return '[Circular]';
                seen.push(val);
            }
            return val;
        });
    }

    window.__mcp_vm_loop_state = {
        version: 2,
        drain: function() {
            sfcFlush();  // drain 前把合并缓冲里残余的装配链落为事件
            spFlush();   // hook 11：逐字符读取链残余落账
            var out = [];
            for (var id in loops) {
                if (!Object.prototype.hasOwnProperty.call(loops, id)) continue;
                var L = loops[id];
                var entry = {
                    loop: id, iterations: L.n,
                    first_ts: L.t0, last_ts: L.t1,
                    states_recorded: L.states.length,
                    truncated: L.truncated,
                    states: L.states
                };
                if (L.times) entry.times = L.times;  // 相对首 tick 毫秒，与 states 平行
                out.push(entry);
            }
            return {
                loops: out,
                dynamic_sources: sources,
                // 代码补丁产物（Task 5）：各 realm 的 globalThis.__mcp_vm_counters
                // 随 drain 一并回收；未定义时序列化阶段自动省略。
                counters: globalThis.__mcp_vm_counters || undefined,
                // wasm 模块证据（hook 8）：哈希/字节/base64/imports/exports 计数
                wasm_modules: wasmModules.length ? wasmModules : undefined,
                // 值变换事件（hook 9）：taint-lite 关联原料
                value_taps: valueTaps.length ? valueTaps : undefined,
                value_taps_overflow: valueTapOverflow || undefined,
                // 字符串装配/读取原语计数（hook 10/11）：独立字段，不进
                // __mcp_vm_counters（补丁自初始化惯用法的兼容约束）；
                // coalesced 是合并事件条数（切片 C2 / 第十二阶段）
                string_primitives: (function() {
                    var sp = null;
                    if (sfcCalls) {
                        sp = { 'fromCharCode.calls': sfcCalls };
                        if (sfcCoalesced)
                            sp['fromCharCode.coalesced'] = sfcCoalesced;
                    }
                    for (var m in spCalls) {
                        if (!Object.prototype.hasOwnProperty.call(spCalls, m))
                            continue;
                        if (!spCalls[m]) continue;
                        if (!sp) sp = {};
                        sp[m + '.calls'] = spCalls[m];
                    }
                    if (spCoalesced) {
                        if (!sp) sp = {};
                        sp['charReader.coalesced'] = spCoalesced;
                    }
                    return sp || undefined;
                })(),
                // opcode 序列记录（第九阶段）：补丁经 __mcp_vm_rec 推入
                trace_seq: traceSeq.length ? traceSeq : undefined,
                trace_seq_overflow: traceSeqOverflow || undefined,
                overhead: {
                    tick_calls: tickCalls,
                    tick_overhead_ns: Math.round(overheadNs),
                    per_tick_ns: tickCalls ? Math.round(overheadNs / tickCalls) : 0
                }
            };
        },
        // 环形引用/BigInt/函数安全序列化：页面对象经 evaluate 回传前先转 JSON 文本
        drainText: function() {
            return safeStringify(this.drain());
        },
        // 聚合 drain：BotGuard 在 bscframe iframe realm 里跑 VM，各 realm 的
        // loops 相互独立，必须从顶层把同源 frame 的 state 一并收上来。
        drainAll: function() {
            var results = [{ realm: 'top', data: this.drain() }];
            var fr = window.frames || [];  // Worker 作用域无 frames
            for (var i = 0; i < fr.length; i++) {
                try {
                    var st = fr[i].__mcp_vm_loop_state;
                    if (st) results.push({ realm: 'frame[' + i + ']', data: st.drain() });
                } catch (e) {
                    results.push({ realm: 'frame[' + i + ']',
                                   error: String(e).substring(0, 120) });
                }
            }
            return results;
        },
        // 异步聚合：在 drainAll（顶层+iframe）基础上，向注册表里的每个
        // Worker 发 drain 请求并带超时等回复；Worker 内嵌套 Worker 由
        // Worker 侧桥递归聚合，realm 层级标记 worker[i]。
        drainAllAsync: function(timeoutMs) {
            var base = this.drainAll();
            var pending = [];
            for (var i = 0; i < workers.length; i++) (function(idx) {
                pending.push(new Promise(function(resolve) {
                    var realm = 'worker[' + idx + ']';
                    var w = workers[idx];
                    var token = 'd' + (++drainSeq) + '_' + idx;
                    var timer = setTimeout(function() {
                        cleanup();
                        resolve({ realm: realm, error: 'drain timeout' });
                    }, timeoutMs || 800);
                    function onMsg(e) {
                        var d = e && e.data;
                        if (d && d.__mcp_vm_drain_reply === token) {
                            cleanup();
                            resolve({ realm: realm, data: d.data });
                        }
                    }
                    function cleanup() {
                        clearTimeout(timer);
                        w.removeEventListener('message', onMsg);
                    }
                    w.addEventListener('message', onMsg);
                    try { w.postMessage({ __mcp_vm_drain: token }); }
                    catch (e) {
                        cleanup();
                        resolve({ realm: realm, error: String(e).substring(0, 120) });
                    }
                }));
            })(i);
            // 第五阶段（reCAPTCHA 实测缺口）：frame realm 里创建的 Worker
            // 注册表在 frame 侧，drainAll 对 frame 只做同步 drain，收不到
            // frame 的 Worker。这里对每个同源 frame 调它自己的
            // drainAllAsync，worker 条目加 frame[i]/ 前缀；frame 自己的
            // 'top' 条目已在 base（drainAll）里，过滤掉避免重复计数。
            // 第十阶段切片 D：跨域 frame 走 postMessage drain 桥——应答侧
            // 是 frame 内运行时注册的 message 监听；超时保留错误条目。
            var bridgedFrames = {};
            var fr = window.frames || [];
            for (var j = 0; j < fr.length; j++) (function(idx) {
                var fw = fr[idx];
                var crossOrigin = false;
                try {
                    var st = fw.__mcp_vm_loop_state;
                    if (st && typeof st.drainAllAsync === 'function') {
                        pending.push(Promise.resolve(st.drainAllAsync(timeoutMs))
                            .then(function(sub) {
                                return (sub || []).filter(function(e) {
                                    return e.realm !== 'top';
                                }).map(function(e) {
                                    e.realm = 'frame[' + idx + ']/' + e.realm;
                                    return e;
                                });
                            })
                            .catch(function(e) {
                                return [{ realm: 'frame[' + idx + ']/drain',
                                          error: String(e).substring(0, 120) }];
                            }));
                    }
                } catch (e) { crossOrigin = true; }
                if (!crossOrigin) return;  // 同源无运行时：跳过（旧行为）
                // 跨域：postMessage drain 请求，校验 e.source 防串台
                pending.push(new Promise(function(resolve) {
                    var token = 'f' + (++drainSeq) + '_' + idx;
                    var timer = setTimeout(function() {
                        cleanup();
                        resolve([{ realm: 'frame[' + idx + ']/drain',
                                   error: 'cross-origin drain timeout' }]);
                    }, timeoutMs || 800);
                    function onMsg(e) {
                        var d = e && e.data;
                        if (!d || d.__mcp_vm_drain_res !== token) return;
                        if (e.source !== fw) return;
                        cleanup();
                        bridgedFrames[idx] = true;
                        var sub = Array.isArray(d.data) ? d.data : [];
                        resolve(sub.map(function(ent) {
                            ent.realm = 'frame[' + idx + ']/' + ent.realm;
                            return ent;
                        }));
                    }
                    function cleanup() {
                        clearTimeout(timer);
                        window.removeEventListener('message', onMsg);
                    }
                    window.addEventListener('message', onMsg);
                    try { fw.postMessage({ __mcp_vm_drain_req: token }, '*'); }
                    catch (e) {
                        cleanup();
                        resolve([{ realm: 'frame[' + idx + ']/drain',
                                   error: String(e).substring(0, 120) }]);
                    }
                }));
            })(j);
            return Promise.all(pending).then(function(workerResults) {
                var flat = [];
                for (var k = 0; k < workerResults.length; k++) {
                    var r = workerResults[k];
                    if (Array.isArray(r)) flat = flat.concat(r);
                    else flat.push(r);
                }
                // 跨域桥成功的 frame，移除 drainAll 残留的同步访问错误条目
                var cleaned = [];
                for (var b = 0; b < base.length; b++) {
                    var be = base[b];
                    var m = /^frame\[(\d+)\]$/.exec(be && be.realm || '');
                    if (m && be.error && bridgedFrames[m[1]]) continue;
                    cleaned.push(be);
                }
                return cleaned.concat(flat);
            });
        },
        drainAllAsyncText: function(timeoutMs) {
            return this.drainAllAsync(timeoutMs).then(function(r) {
                return safeStringify(r);
            });
        },
        drainAllText: function() {
            return safeStringify(this.drainAll());
        },
        clear: function() {
            loops = {};
            sources = [];
            tickCalls = 0;
            overheadNs = 0;
        }
    };
})(window);
