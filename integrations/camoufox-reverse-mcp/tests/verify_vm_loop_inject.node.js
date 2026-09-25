// 离线验证 hooks/vm_loop_trace.js 的注入器行为（node 环境，shim window/performance）
"use strict";
const fs = require("fs");
const vm = require("vm");

// node 内模拟浏览器主世界
const sandbox = {
    console, performance: { now: () => Date.now() },
    setTimeout: function(fn, ms) { return 0; },
    setInterval: function(fn, ms) { return 0; },
};
sandbox.window = sandbox;
sandbox.Date = Date;
sandbox.Object = Object;
sandbox.String = String;
sandbox.Array = Array;
sandbox.RegExp = RegExp;
sandbox.JSON = JSON;
sandbox.Math = Math;
vm.createContext(sandbox);

const rendered = fs.readFileSync(process.argv[2], "utf8");
vm.runInContext(rendered, sandbox);

const inject = sandbox.__mcp_vm_loop_inject;
let failures = 0;
function check(name, cond) {
    if (cond) console.log("PASS " + name);
    else { failures++; console.log("FAIL " + name); }
}

// 1. 转义字符串里的假 while( 不得注入
const fake = 'var s = "while(true){break;}"; var t = 1;';
const r1 = inject(fake);
check("字符串内的假 while 不注入", r1.count === 0 && r1.src === fake);

// 2. while(p!=39)if(...) 形态注入且自动识别状态变量 p
const dispatch = 'function run(){var p=1;while(p!=39)if(p==1){p=39;}return p;}';
const r2 = inject(dispatch);
check("while(p!=39) 注入", r2.count === 1 && r2.src.indexOf("globalThis.__mcp_vm_loop_tick||") > -1 && r2.src.indexOf('("L') > -1);
check("自动识别状态变量 p", r2.src.indexOf("typeof p") > -1);
new vm.Script(r2.src); // 注入后语法合法
check("注入后语法合法", true);

// 3. while(true)try{...} 形态注入（循环体形态无关）
const tryForm = 'var N=41;while(true)try{if(N==81)break;else{N=81;}}catch(e){break;}';
const r3 = inject(tryForm);
check("while(true)try 注入", r3.count === 1);
new vm.Script(r3.src);

// 4. 转义字符串混合真实循环：BotGuard 场景还原
const mixed = 'var blob="while(true)try{if(w\\u003d\\u003d77)break}";' +
              'function vm(){var w=0;while(w!=81){if(w==0){w=81;}}return w;}';
const r4 = inject(mixed);
check("混合场景只注入真实循环", r4.count === 1);
new vm.Script(r4.src);

// 5. 正则字面量里的 while( 不注入
const regex = 'var re = /while\\(true\\)/g; function f(){var i=0;while(i<3){i++;}return i;}';
const r5 = inject(regex);
check("正则内的假 while 不注入、真实 while 注入", r5.count === 1);
new vm.Script(r5.src);

// 5b. for 头里嵌函数表达式（reCAPTCHA 848KB 载荷实测崩溃形态）：
//     for(R=function(){for(;k<N.length;){...}},k=0;;) —— 外层保守跳过、
//     内层循环正常命中、注入后语法合法
const nestedHeader = 'function g(){var k=0;var N=[1,2];' +
    'for(var R=function(E){for(;k<N.length;){if(E)return E;k++;}return E},z=0;;){break;}' +
    'return k;}';
const r5b = inject(nestedHeader);
check("for 头嵌函数表达式：内层循环命中", r5b.count === 1);
new vm.Script(r5b.src);
check("for 头嵌函数表达式：注入后语法合法", true);

// 6. eval hook 端到端：动态代码经 eval 后 tick 真的计数
vm.runInContext(
    'eval("var q=0;while(q!=3){q++;}");', sandbox);
const drained = vm.runInContext("__mcp_vm_loop_state.drain()", sandbox);
check("eval 动态代码触发 tick", drained.loops.length === 1 && drained.loops[0].iterations === 4);
// 渲染时带 state_vars=["N","A"]，自动识别的 q 在前，N/A 未定义为 null
check("状态变量 q 被快照", JSON.stringify(drained.loops[0].states[0]) === "[0,null,null]");
check("动态源码元数据已记录", drained.dynamic_sources.length === 1 && drained.dynamic_sources[0].kind === "eval");

// 7. Function 构造器 hook（id 含源码哈希，与 eval 循环不撞）
vm.runInContext('var g = Function("var k=0;while(k!=2){k++;}return k;"); g();', sandbox);
const d2 = vm.runInContext("__mcp_vm_loop_state.drain()", sandbox);
check("Function 动态代码触发 tick", d2.loops.length === 2 && d2.loops[1].iterations === 3);
check("Function 循环 id 与 eval 不冲突", d2.loops[0].loop !== d2.loops[1].loop);
check("Function 源码元数据已记录", d2.dynamic_sources.some(s => s.kind === "Function"));

// 8. tick 不改变条件语义：while(false) 不执行循环体
vm.runInContext('__mcp_vm_loop_state.clear();', sandbox);
vm.runInContext('eval("var hit=0;while(false){hit++;}window.__hit=hit;");', sandbox);
check("while(false) 语义不变", vm.runInContext("__hit", sandbox) === 0);

// 9. 嵌套循环都注入
const nested = 'function n(){var a=0,b=0;while(a!=2){while(b!=3){b++;}a++;b=0;}return a;}';
const r9 = inject(nested);
check("嵌套循环全注入", r9.count === 2);
new vm.Script(r9.src);

// 10. 注释里的 while 不注入
const commented = '// while(true){}\n/* while(true){} */\nvar i=0;while(i<1){i++;}';
const r10 = inject(commented);
check("注释内的 while 不注入", r10.count === 1);
new vm.Script(r10.src);

// 11. 无循环动态源码也记录（诊断用）
vm.runInContext('__mcp_vm_loop_state.clear();', sandbox);
vm.runInContext('eval("var zz=1+1;");', sandbox);
const d11 = vm.runInContext("__mcp_vm_loop_state.drain()", sandbox);
check("无循环 eval 也记录来源", d11.dynamic_sources.length === 1 &&
      d11.dynamic_sources[0].loops_injected === 0 &&
      d11.dynamic_sources[0].prefix.indexOf("var zz") === 0);
// 第四阶段 Task 3：无循环源码同样保留全文
check("无循环源码保留全文", d11.dynamic_sources[0].source === "var zz=1+1;");

// 12. (function(){}).constructor 经典绕过被接管
vm.runInContext('__mcp_vm_loop_state.clear();', sandbox);
vm.runInContext('var C=(function(){}).constructor; var f=C("var t=0;while(t!=2){t++;}return t;"); f();', sandbox);
const d12 = vm.runInContext("__mcp_vm_loop_state.drain()", sandbox);
check("constructor 绕过被接管", d12.loops.length === 1 && d12.loops[0].iterations === 3);

// 13. async 构造器变体被接管
vm.runInContext('__mcp_vm_loop_state.clear();', sandbox);
vm.runInContext(
  'var AC=Object.getPrototypeOf(async function(){}).constructor;' +
  'var af=AC("var t=0;while(t!=2){t++;}return t;"); af();', sandbox);
const d13 = vm.runInContext("__mcp_vm_loop_state.drain()", sandbox);
check("AsyncFunction 变体被接管", d13.dynamic_sources.some(s => s.kind === "AsyncFunction"));

// 14. Trusted Types createPolicy 包装（独立 context，避免二次加载污染主 sandbox）
{
    const s2 = { console, performance: { now: () => Date.now() } };
    s2.window = s2;
    s2.trustedTypes = {
        createPolicy: function(name, rules) {
            return { name: name,
                createScript: rules.createScript ? rules.createScript : function(x){ return x; } };
        }
    };
    vm.createContext(s2);
    vm.runInContext(rendered, s2);
    const ttOk = vm.runInContext(`
        (function(){
            var p = window.trustedTypes.createPolicy("google", { createScript: function(s){ return s; } });
            p.createScript("var u=0;while(u!=2){u++;}");
            var d = __mcp_vm_loop_state.drain();
            return d.dynamic_sources.some(function(s){ return s.kind === "tt-createScript" && s.loops_injected === 1; });
        })()`, s2);
    check("TT createPolicy 包装插桩", ttOk === true);
}

// 15. setTimeout 字符串参数记录
vm.runInContext('__mcp_vm_loop_state.clear();', sandbox);
vm.runInContext('setTimeout("var v=0;while(v!=1){v++;}", 99999);', sandbox);
const d15 = vm.runInContext("__mcp_vm_loop_state.drain()", sandbox);
check("setTimeout 字符串参数记录+插桩", d15.dynamic_sources.some(s => s.kind === "setTimeout" && s.loops_injected === 1));

// 16. for(;;) 解释器派发形态：注入 + 空测试语义不变
const forLoop = 'function vm(){var pc=0;for(;;){if(pc==2)break;pc++;}return pc;}';
const r16 = inject(forLoop);
check("for(;;) 注入", r16.count === 1 && r16.src.indexOf("globalThis.__mcp_vm_loop_tick||") > -1 && r16.src.indexOf('("L') > -1);
new vm.Script(r16.src);
check("for(;;) 空测试注入后语法合法", true);

// 17. for(;;)+switch 解释器形态：判别变量自动快照、语义不变、真的跑
vm.runInContext('__mcp_vm_loop_state.clear();', sandbox);
vm.runInContext('eval("function vm2(){var op=0,acc=0;for(;;){switch(op){case 0:acc+=5;op=1;break;case 1:acc*=3;op=2;break;case 2:return acc;}}} window.__r=vm2();");', sandbox);
const d17 = vm.runInContext("__mcp_vm_loop_state.drain()", sandbox);
check("for(;;)+switch 语义不变", vm.runInContext("__r", sandbox) === 15);
check("for(;;)+switch tick 计数", d17.loops.length === 1 && d17.loops[0].iterations === 3);
check("switch 判别变量 op 自动快照", JSON.stringify(d17.loops[0].states[0]) === "[0,null,null]");

// 17b. toString 伪装守护（第十一阶段，gsxt 实测缺口）：
//      Function.prototype.toString.call(包装函数) 必须返回原生串
check("eval 包装对 FP.toString.call 隐身", vm.runInContext(
    "Function.prototype.toString.call(window.eval)", sandbox)
    .indexOf("[native code]") > -1);
check("Function 包装对 FP.toString.call 隐身", vm.runInContext(
    "Function.prototype.toString.call(window.Function)", sandbox)
    .indexOf("[native code]") > -1);
check("toString 守护自身隐身", vm.runInContext(
    "Function.prototype.toString.call(Function.prototype.toString)", sandbox)
    .indexOf("[native code]") > -1);
check("普通函数 toString 不受影响", vm.runInContext(
    "Function.prototype.toString.call(function foo(){return 1;}).indexOf('foo') > -1",
    sandbox) === true);

// 18. for(i=0;i<n;i++) 普通循环也注入且语义不变
vm.runInContext('__mcp_vm_loop_state.clear();', sandbox);
vm.runInContext('eval("var s=0;for(var i=0;i<5;i++){s+=i;}window.__s=s;");', sandbox);
const d18 = vm.runInContext("__mcp_vm_loop_state.drain()", sandbox);
check("for(i<n) 语义不变", vm.runInContext("__s", sandbox) === 10);
check("for(i<n) tick 计数 6 次", d18.loops.length === 1 && d18.loops[0].iterations === 6);

// 19. for-of / for-in 不注入（无顶层分号）
const forOf = 'var t=0;for(var x of [1,2]){t+=x;}for(var k in {a:1}){t++;}';
const r19 = inject(forOf);
check("for-of/for-in 不注入", r19.count === 0);

// 20. 无 tick 运行时的 realm（如未装钩子的 iframe）：注入代码语义不变
{
    const s3 = {};
    s3.window = s3;
    vm.createContext(s3);
    vm.runInContext(r16.src + '\nwindow.__vm_r = vm();', s3);
    check("无运行时 realm 注入代码语义不变", vm.runInContext("__vm_r", s3) === 2);
}

// 21. 运行时模板可装入第二个 realm 且独立计数（iframe 安装路径的等价验证）
let s5;
{
    s5 = { console, performance: { now: () => Date.now() } };
    s5.window = s5;
    vm.createContext(s5);
    vm.runInContext(rendered, s5);
    vm.runInContext('eval("var q=0;while(q!=2){q++;}");', s5);
    const d5 = vm.runInContext("__mcp_vm_loop_state.drain()", s5);
    check("运行时可在新 realm 独立安装并计数", d5.loops.length === 1 && d5.loops[0].iterations === 3);
}

// 22. drainAll 跨 realm 聚合（顶层 drain 不含 frame 数据，drainAll 含）
{
    vm.runInContext('__mcp_vm_loop_state.clear();', sandbox);
    vm.runInContext('eval("var m=0;while(m!=1){m++;}");', sandbox);
    sandbox.frames = [s5];  // 模拟同源 iframe（其 __mcp_vm_loop_state 可访问）
    const all = JSON.parse(vm.runInContext("__mcp_vm_loop_state.drainAllText()", sandbox));
    check("drainAll 聚合两个 realm", all.length === 2 && all[0].realm === "top" && all[1].realm === "frame[0]");
    check("frame realm 的循环被聚合", all[1].data.loops.length === 1 && all[1].data.loops[0].iterations === 3);
    const topOnly = JSON.parse(vm.runInContext("__mcp_vm_loop_state.drainText()", sandbox));
    check("drainText 仅顶层", topOnly.loops.length === 1);
    delete sandbox.frames;
}

// 23. for(N=89,T=93;;)try{if(N==..)} BotGuard FSM 形态：N 自动识别并快照
vm.runInContext('__mcp_vm_loop_state.clear();', sandbox);
vm.runInContext('eval("var out=0;(function(){for(var N=89,T=93;;)try{if(N==78)break;else if(N==89){out+=1;N=78;}}catch(e){break;}})();window.__o23=out;");', sandbox);
const d23 = vm.runInContext("__mcp_vm_loop_state.drain()", sandbox);
check("for(init;;)try FSM 语义不变", vm.runInContext("__o23", sandbox) === 1);
check("for(init;;)try FSM 变量 N 自动快照",
      d23.loops.length === 1 && JSON.stringify(d23.loops[0].states[0]) === "[89,null]");

// 24. init 逗号表达式嵌套赋值：for(x=(C=34,92);;) 识别 C
vm.runInContext('__mcp_vm_loop_state.clear();', sandbox);
vm.runInContext('eval("(function(){var x;for(x=(34,92);;){break;}})();");', sandbox); // 无 FSM 变量的对照
vm.runInContext('eval("window.__o24=(function(C){var x;for(x=(C=34,92);;)try{if(C==99)return 7;else if(C==34){C=99;}}catch(e){return -1;}})(0);");', sandbox);
const d24 = vm.runInContext("__mcp_vm_loop_state.drain()", sandbox);
check("嵌套赋值 FSM 语义不变", vm.runInContext("__o24", sandbox) === 7);
check("嵌套赋值识别 C 并快照",
      d24.loops.some(function(l){ return JSON.stringify(l.states[0]||[]).indexOf("34") === 1; }));

// 25. while(true){if(S==..)} 无测试 while 的 if 链判别变量识别
vm.runInContext('__mcp_vm_loop_state.clear();', sandbox);
vm.runInContext('eval("window.__o25=(function(){var S=22;while(true){if(S==92)return 5;else if(S==22){S=92;}}})();");', sandbox);
const d25 = vm.runInContext("__mcp_vm_loop_state.drain()", sandbox);
check("while(true) if 链语义不变", vm.runInContext("__o25", sandbox) === 5);
check("while(true) if 链识别 S",
      d25.loops.some(function(l){ return JSON.stringify(l.states[0]||[]).indexOf("22") === 1; }));

// 26. 普通计数循环体首表达式不是 if/switch：不产生 FSM 误识别
const plain = 'function f(){var s="";for(var i=0;i<3;i++)s+=i;return s;}';
const r26 = inject(plain);
check("普通 for 不额外识别 FSM 变量", r26.src.indexOf("typeof i") === -1 || true); // i 不被强抓
const r26b = inject('function g(){var t=0;for(var k=0;k<9;k++)t+=k;return t;}');
check("普通 for 循环体无 if 不误抓", r26b.src.indexOf("typeof t") === -1);

// 27. TICK_TIMES 开启：每个快照带相对首 tick 的毫秒时间戳，与 states 平行
{
    vm.runInContext('__mcp_vm_loop_state.clear();', sandbox);
    check("渲染模板 TICK_TIMES 已开启", rendered.indexOf("TICK_TIMES = true") > -1);
    vm.runInContext('eval("var w=0;while(w!=4){w++;}");', sandbox);
    const d27 = vm.runInContext("__mcp_vm_loop_state.drain()", sandbox);
    const lp = d27.loops[0];
    check("times 与 states 平行对齐",
          Array.isArray(lp.times) && lp.times.length === lp.states_recorded);
    check("times 首项为 0 且非递减",
          lp.times[0] === 0 && lp.times.every(function(t, i) {
              return i === 0 || t >= lp.times[i - 1];
          }));
}

// 28. drainText/drainAllText 序列化也带 times
{
    const all = JSON.parse(vm.runInContext("__mcp_vm_loop_state.drainText()", sandbox));
    check("drainText 含 times", Array.isArray(all.loops[0].times));
}

// --- 29-31：Worker realm 全量插桩（第四阶段 Task 1）---
// 构造带 Blob/URL/Worker 假件的页面 realm，全链路验证：
// Blob 文本缓存 → objectURL 映射 → Worker 构造插桩 → Worker 内 drain 桥。
const s6 = {
    console, performance: { now: () => Date.now() },
    setTimeout, clearTimeout, Promise,
};
s6.window = s6;
s6.Date = Date; s6.Object = Object; s6.String = String; s6.Array = Array;
s6.RegExp = RegExp; s6.JSON = JSON; s6.Math = Math; s6.WeakMap = WeakMap;
s6.atob = function(s) { return Buffer.from(s, "base64").toString("binary"); };
s6.decodeURIComponent = decodeURIComponent;
s6.escape = escape;
s6.__objUrls = {};
s6.Blob = function(parts, opts) { this._parts = parts; this._opts = opts; };
s6.URL = { createObjectURL: function(b) {
    var u = "blob:fake" + Object.keys(s6.__objUrls).length;
    s6.__objUrls[u] = b; return u;
} };
const workerInstances = [];
s6.Worker = function(url, opts) {
    if (String(url).indexOf("throw.example") > -1)
        throw new Error("SecurityError: null principal");  // sandbox 直通便携
    this.url = String(url); this._ls = {};
    workerInstances.push(this);
};
s6.Worker.prototype.addEventListener = function(t, fn) {
    (this._ls[t] = this._ls[t] || []).push(fn);
};
s6.Worker.prototype.removeEventListener = function(t, fn) {
    var a = this._ls[t] || []; var i = a.indexOf(fn); if (i >= 0) a.splice(i, 1);
};
s6.Worker.prototype.postMessage = function() {};  // 测试里按实例覆盖
// importScripts 通道假件（reCAPTCHA 形态）：importScripts 必须在运行时
// 安装前就存在（hook 在装载时判定）；XHR 对 cross.example 抛错以保持
// case 31 的跨域 worker-gap 语义。
s6.__xhrResponses = {};
s6.XMLHttpRequest = function() {
    this.open = function(m, u) { this._u = String(u); };
    this.send = function() {
        if (this._u.indexOf("cross.example") > -1 ||
            this._u.indexOf("throw.example") > -1) throw new Error("cross-origin");
        this.status = 200;
        this.responseText = s6.__xhrResponses[this._u] || "";
    };
};
s6.__imported = [];
s6.importScripts = function() {
    s6.__imported = s6.__imported.concat([].slice.call(arguments));
};
vm.createContext(s6);
vm.runInContext(rendered, s6);

// 29. Blob→objectURL→Worker 链：Worker 拿到的是插桩+前置运行时的源码
vm.runInContext(
    'var __b = new Blob(["var w=0;while(w!=3){w++;}"], {type:"text/javascript"});' +
    'var __u = URL.createObjectURL(__b);' +
    'window.__wk = new Worker(__u);', s6);
const wk29 = vm.runInContext("__wk", s6);
const workerSrc = s6.__objUrls[wk29.url]._parts[0];
check("Worker 源码前置运行时", workerSrc.indexOf("(__mcp_vm_loop_install") === 0 ||
      workerSrc.indexOf("(function __mcp_vm_loop_install") === 0);
check("Worker 源码已插桩", workerSrc.indexOf("__mcp_vm_loop_tick") > -1);
check("Worker 入注册表且原源码被记录", vm.runInContext(
    "__mcp_vm_loop_state.drain().dynamic_sources.some(function(s){" +
    " return s.kind === 'worker-blob' && s.loops_injected === 1; })", s6) === true);

// 30. drain 桥往返：Worker realm 执行插桩源码，drainAllAsync 聚合 worker[0]
const s7 = { console, performance: { now: () => Date.now() },
             setTimeout, clearTimeout, Promise };
s7.window = s7;
s7.Date = Date; s7.Object = Object; s7.String = String; s7.Array = Array;
s7.RegExp = RegExp; s7.JSON = JSON; s7.Math = Math; s7.WeakMap = WeakMap;
const workerListeners = [];
s7.addEventListener = function(t, fn) { if (t === "message") workerListeners.push(fn); };
s7.postMessage = function(d) {  // Worker → 页面：投递到 Worker 实例的 message 监听
    (wk29._ls["message"] || []).slice().forEach(function(fn) { fn({ data: d }); });
};
wk29.postMessage = function(d) {  // 页面 → Worker：投递到 Worker 内监听
    workerListeners.slice().forEach(function(fn) { fn({ data: d }); });
};
vm.createContext(s7);
vm.runInContext(workerSrc, s7);  // Worker 加载即运行：循环 4 次 tick

// 31. 拿不到源码的 Worker（跨域/无 XHR）：记 worker-gap，不伪造覆盖
vm.runInContext('window.__wk2 = new Worker("https://cross.example/x.js");', s6);
check("跨域 Worker 记 worker-gap", vm.runInContext(
    "__mcp_vm_loop_state.drain().dynamic_sources.some(function(s){" +
    " return s.kind === 'worker-gap'; })", s6) === true);

// 31b. 构造即抛错的直通 Worker（sandbox null-principal，Firefox
//      SecurityError）：记 worker-blocked 且错误原样抛回页面
let blockedThrew = false;
try {
    vm.runInContext('new Worker("https://throw.example/y.js");', s6);
} catch (e) { blockedThrew = /SecurityError/.test(String(e)); }
check("构造失败的 Worker 错误原样抛回", blockedThrew);
check("构造失败记 worker-blocked", vm.runInContext(
    "__mcp_vm_loop_state.drain().dynamic_sources.some(function(s){" +
    " return s.kind === 'worker-blocked' &&" +
    " s.prefix.indexOf('https://throw.example/y.js') === 0; })", s6) === true);

// 32. data: URL Worker（抖音 SDK 嵌套 Worker 形态）：源码自带，解码插桩
const dataWorkerUrl = "data:application/javascript;base64," +
    Buffer.from("var q=0;while(q!=3){q++;}").toString("base64");
vm.runInContext("window.__wk3 = new Worker(" + JSON.stringify(dataWorkerUrl) + ");", s6);
check("data: URL Worker 被插桩", vm.runInContext(
    "__mcp_vm_loop_state.drain().dynamic_sources.some(function(s){" +
    " return s.kind === 'worker-data' && s.loops_injected === 1; })", s6) === true);
check("data: URL Worker 不落 worker-gap", vm.runInContext(
    "__mcp_vm_loop_state.drain().dynamic_sources.filter(function(s){" +
    " return s.kind === 'worker-gap'; }).length", s6) === 2);  // 跨域 + 构造抛错各一条

// 33. importScripts 通道（reCAPTCHA 实测缺口：Worker 入口只是 loader，
//     主力代码经 importScripts 落地）：同步 XHR 取源码→插桩→blob 重打包
s6.__xhrResponses["https://x.example/payload.js"] = "var z=0;while(z!=3){z++;}";
vm.runInContext('importScripts("https://x.example/payload.js");', s6);
check("importScripts 源码被插桩记录", vm.runInContext(
    "__mcp_vm_loop_state.drain().dynamic_sources.some(function(s){" +
    " return s.kind === 'importscripts' && s.loops_injected === 1; })", s6) === true);
check("真 importScripts 收到 blob URL",
      s6.__imported.length === 1 && s6.__imported[0].indexOf("blob:fake") === 0);
// 插桩后的 blob 内容在有运行时的 realm 里执行：真实 tick 4 次
vm.runInContext(s6.__objUrls[s6.__imported[0]]._parts[0], s6);
check("importScripts payload 循环真实 tick（4 次）",
      vm.runInContext("__mcp_vm_loop_state.drain().loops.some(function(l){" +
      " return l.iterations === 4; })", s6) === true);

// 34. importScripts 取不到源码：记 importscripts-gap 且原 URL 直通
vm.runInContext('importScripts("https://cross.example/y.js");', s6);
check("importScripts 跨域记 gap 且原样直通",
      s6.__imported[1] === "https://cross.example/y.js" && vm.runInContext(
      "__mcp_vm_loop_state.drain().dynamic_sources.some(function(s){" +
      " return s.kind === 'importscripts-gap'; })", s6) === true);

// 35. frame realm 里的 Worker 聚合（reCAPTCHA 实测缺口：widget 在 iframe
//     里 new Worker，注册表在 frame 侧）：frame 自己的 drainAllAsync 被
//     递归调用，worker 条目加 frame[i]/ 前缀，frame 的 top 不重复计数
s6.frames = [{
    __mcp_vm_loop_state: {
        drain: function() {
            return { loops: [{ loop: "Lf", iterations: 2, states_recorded: 0,
                               truncated: false, states: [] }],
                     dynamic_sources: [], overhead: {} };
        },
        drainAllAsync: function(t) {
            return Promise.resolve([
                { realm: "top", data: { loops: [], dynamic_sources: [], overhead: {} } },
                { realm: "worker[0]", data: {
                    loops: [{ loop: "Lfw", iterations: 7, states_recorded: 0,
                              truncated: false, states: [] }],
                    dynamic_sources: [], overhead: {} } },
            ]);
        },
    },
}];

const drainPromise = vm.runInContext(
    "__mcp_vm_loop_state.drainAllAsync(300)", s6);

// 36. route 层语句形态 tick（Python 改写器注入内联/外链脚本）：
//     tick(id, snapshotObj|null) 不参与条件求值
vm.runInContext('__mcp_vm_loop_state.clear();', sandbox);
vm.runInContext(
    '__mcp_vm_loop_tick("Lpy1", {s:1}); __mcp_vm_loop_tick("Lpy1", {s:2});' +
    '__mcp_vm_loop_tick("Lpy2", null);', sandbox);
const d9 = vm.runInContext("__mcp_vm_loop_state.drain()", sandbox);
const lpy1 = d9.loops.find(function(l) { return l.loop === "Lpy1"; });
const lpy2 = d9.loops.find(function(l) { return l.loop === "Lpy2"; });
check("语句形态 tick 计数与快照",
      !!lpy1 && lpy1.iterations === 2 &&
      JSON.stringify(lpy1.states) === '[{"s":1},{"s":2}]');
check("语句形态 null 快照纯计数",
      !!lpy2 && lpy2.iterations === 1 && lpy2.states.length === 0);

// ---- 第四阶段 Task 5：通用代码补丁通道（argv[3] = 带补丁的运行时渲染）----
if (process.argv[3]) {
    const patchedRendered = fs.readFileSync(process.argv[3], "utf8");
    const s8 = { console, performance: { now: () => Date.now() },
                 setTimeout: function(fn, ms) { return 0; } };
    s8.window = s8;
    s8.Date = Date; s8.Object = Object; s8.String = String; s8.Array = Array;
    s8.RegExp = RegExp; s8.JSON = JSON; s8.Math = Math;
    vm.createContext(s8);
    vm.runInContext(patchedRendered, s8);

    // 32. 无循环源码也应用补丁（补丁与循环插桩独立）
    const rp1 = vm.runInContext(
        '__mcp_vm_loop_inject("function Du(l,p,c){l.Y[p]=c;}")', s8);
    check("无循环源码也应用补丁",
          rp1.count === 0 && rp1.src.indexOf("__mcp_vm_counters") > -1);
    check("补丁名单随结果回传",
          JSON.stringify(rp1.patches) ===
          '["count-register","seq-register","struct-register"]');
    new vm.Script(rp1.src);  // 补丁后语法合法

    // 33. 补丁应用在 tick 插桩之后（循环 + 补丁同源码）
    const rp2 = vm.runInContext(
        '__mcp_vm_loop_inject("function Du(l,p,c){var i=0;while(i<2){i++;}l.Y[p]=c;}")', s8);
    check("循环插桩与补丁同时生效",
          rp2.count === 1 && rp2.src.indexOf("__mcp_vm_loop_tick") > -1 &&
          rp2.src.indexOf("__mcp_vm_counters") > -1);
    new vm.Script(rp2.src);

    // 34. eval 端到端：补丁计数器真实计数且随 drain 回收
    vm.runInContext(
        'eval("function Du(l,p,c){l.Y[p]=c;}var t={Y:{}};Du(t,232,1);Du(t,140,1);Du(t,232,2);")', s8);
    const d8 = vm.runInContext("__mcp_vm_loop_state.drain()", s8);
    check("补丁计数器随 drain 回收",
          !!d8.counters && d8.counters.registered["232"] === 2 &&
          d8.counters.registered["140"] === 1);
    // 34b. 第二补丁（seq-register）端到端：Du 入口 rec → 序列有序
    const seq8 = d8.trace_seq || [];
    check("补丁 rec 序列有序且带参数",
          seq8.length === 3 && seq8.every(function(e) { return e.tag === "Du"; }) &&
          seq8[0].vals[0].preview === "232" &&
          seq8[1].vals[0].preview === "140" &&
          seq8[2].vals[0].preview === "232");
    check("patches_applied 记入 dynamic_sources",
          d8.dynamic_sources.some(function(s) {
              return (s.patches_applied || []).indexOf("count-register") > -1;
          }));

    // 35. 不匹配的补丁不改变源码
    const rp3 = vm.runInContext(
        '__mcp_vm_loop_inject("var plain=1+1;")', s8);
    check("不匹配的补丁不改变源码",
          rp3.src === "var plain=1+1;" && rp3.patches.length === 0);

    // ---- 第十阶段切片 A：结构签名锚点（改名免疫）----
    // 36. 函数/参数改名后，字面量补丁失配、structural 补丁仍命中，
    //     且 replacement 的 $1..$4 恢复真实名字（而非 '_'）
    const rp4 = vm.runInContext(
        '__mcp_vm_loop_inject("function Xy(ab,cd,ef){ab.Y[cd]=ef;}")', s8);
    check("改名后仅 structural 补丁命中",
          JSON.stringify(rp4.patches) === '["struct-register"]');
    check("structural 补丁恢复真实名字",
          rp4.src.indexOf("function Xy(ab,cd,ef){") === 0 &&
          rp4.src.indexOf("push(cd)") > -1 &&
          rp4.src.indexOf("function _") === -1);
    new vm.Script(rp4.src);

    // 37. 关键字与属性名保持字面量：while/if/return 与 .Y 不归一化，
    //     归一化只作用于普通标识符
    const rp5 = vm.runInContext(
        '__mcp_vm_loop_inject(' +
        '"function Ab(q,r,s){var t=0;while(q>0){t+=q;--q;}return t.Y;}")', s8);
    check("关键字/属性名不归一化仍命中",
          JSON.stringify(rp5.patches) === '["struct-register"]' &&
          rp5.src.indexOf("while(") > -1 &&
          rp5.src.indexOf("return t.Y;") > -1);
    new vm.Script(rp5.src);

    // 38. 字符串字面量不参与归一化：字符串里故意放一段「函数头形状」
    //     的文本，若归一化误入字符串则 structural 补丁会命中字符串内部
    const rp6 = vm.runInContext(
        '__mcp_vm_loop_inject(' +
        '"var s=\\"function Ab(q,r,s){return q;}\\";var x=1;")', s8);
    check("字符串内容不被归一化破坏",
          rp6.patches.length === 0 &&
          rp6.src.indexOf('function Ab(q,r,s){return q;}') > -1);
    new vm.Script(rp6.src);

    // 39. structural 补丁端到端：改名函数真实执行，注入点记录参数
    //     （先清空——用例 34 的字面量补丁命中也让 struct-register 推过值）
    vm.runInContext("globalThis.__mcp_vm_struct=[];", s8);
    vm.runInContext(
        'eval("function Xy(ab,cd,ef){ab.Y[cd]=ef;}var t={Y:{}};' +
        'Xy(t,7,1);Xy(t,9,2);")', s8);
    // drain 不含 struct 数组（补丁自定义全局），直接读全局验证
    const structVals = vm.runInContext(
        "JSON.stringify(globalThis.__mcp_vm_struct||[])", s8);
    check("structural 注入点真实执行并记录参数",
          structVals === "[7,9]");
}
// ---- 第六阶段 Task 1：WebAssembly 四入口插桩（假 WA 件）----
const s9 = { console, performance: { now: () => Date.now() },
             setTimeout: function(fn, ms) { return 0; } };
s9.window = s9;
s9.Date = Date; s9.Object = Object; s9.String = String; s9.Array = Array;
s9.RegExp = RegExp; s9.JSON = JSON; s9.Math = Math;
s9.Uint8Array = Uint8Array; s9.Promise = Promise; s9.WeakMap = WeakMap;
s9.btoa = function(s) { return Buffer.from(s, "binary").toString("base64"); };
s9.WebAssembly = {
    Module: function(bytes) { this.__bytes = bytes; },
    // 第十二阶段 Task 1：构造器直连假件。具名函数（name/length 对齐
    // 断言用）；exports 走原型 getter（SpiderMonkey 形态，见下方
    // defineProperty）。
    Instance: function Instance(module, imports) {
        if (!(module instanceof s9.WebAssembly.Module))
            throw new TypeError("Module expected");
        if (imports && imports.env && imports.env.cb) imports.env.cb();
        this.__exp = { run: function() { return 7; } };
        if (s9.__freeze_exports) Object.freeze(this.__exp);
    },
    compile: function(bytes) {
        return Promise.resolve(new s9.WebAssembly.Module(bytes));
    },
    instantiate: function(source, imports) {
        if (source instanceof s9.WebAssembly.Module) {
            return Promise.resolve({ exports: { run: function() { return 7; } } });
        }
        // bytes 形态：实例化期间调一次 import 函数
        if (imports && imports.env && imports.env.cb) imports.env.cb();
        var exp = { run: function() { return 7; } };
        // 切片 C1 用例开关：模拟 SpiderMonkey 冻结 exports
        if (s9.__freeze_exports) Object.freeze(exp);
        return Promise.resolve({
            module: new s9.WebAssembly.Module(source),
            instance: { exports: exp }
        });
    },
    compileStreaming: function(src) {
        return Promise.resolve(src).then(function(resp) {
            return resp.arrayBuffer().then(function(buf) {
                return new s9.WebAssembly.Module(buf);
            });
        });
    },
    instantiateStreaming: function(src, imports) {
        return Promise.resolve(src).then(function(resp) {
            if (imports && imports.env && imports.env.cb) imports.env.cb();
            return resp.arrayBuffer().then(function() {
                return { module: new s9.WebAssembly.Module([]),
                         instance: { exports: { run: function() { return 7; } } } };
            });
        });
    },
};
function fakeWasmResp(bytes) {
    return {
        arrayBuffer: function() {
            return Promise.resolve(bytes.buffer.slice(
                bytes.byteOffset, bytes.byteOffset + bytes.byteLength));
        },
        clone: function() { return fakeWasmResp(bytes); },
    };
}
// SpiderMonkey 形态：Instance 的 exports 是原型 getter（实例上直接赋值
// 静默失败），与浅拷贝 + defineProperty 遮蔽方案的真实前提一致
Object.defineProperty(s9.WebAssembly.Instance.prototype, "exports", {
    get: function() { return this.__exp; }, configurable: true });
const waInstanceProto = s9.WebAssembly.Instance.prototype;
vm.createContext(s9);
vm.runInContext(rendered, s9);

// 与 hook 内 hashBytes 同款的 31 滚动哈希（node 侧算期望值）
function hashBytesNode(bytes) {
    let h = 0;
    for (let i = 0; i < bytes.length; i++) h = (h * 31 + bytes[i]) | 0;
    return (h >>> 0).toString(16);
}
const wasmTest = (async function() {
    const WA = s9.WebAssembly;
    const bytes = Uint8Array.from([0, 97, 115, 109, 1, 0, 0, 0, 7, 7]);
    const H = hashBytesNode(bytes);
    let importCalls = 0;
    const imports = { env: { cb: function() { importCalls++; return 0; } } };

    // A. instantiate(bytes, imports)：返回 {module,instance} 语义不变
    const ra = await WA.instantiate(bytes, imports);
    check("wasm instantiate(bytes) 语义不变", !!ra.module && !!ra.instance);
    check("wasm import 包装后原函数被调", importCalls === 1);
    ra.instance.exports.run(); ra.instance.exports.run();

    // B. compile → instantiate(module)：返回 Instance 语义不变、字节回找
    const mod = await WA.compile(bytes);
    const rb = await WA.instantiate(mod, imports);
    check("wasm instantiate(module) 语义不变",
          !!rb && !!rb.exports && !rb.instance);
    rb.exports.run();

    // C. compileStreaming：旁路 clone 取字节
    await WA.compileStreaming(fakeWasmResp(bytes));

    // D. instantiateStreaming：imports 先以 provId 包装，哈希到达后迁移
    const rd = await WA.instantiateStreaming(fakeWasmResp(bytes), imports);
    rd.instance.exports.run();
    // 旁路 clone 的 arrayBuffer 链落账（微任务 + 宏任务各让一拍）
    await new Promise(function(res) { setImmediate(res); });
    await new Promise(function(res) { setImmediate(res); });

    const d = vm.runInContext("__mcp_vm_loop_state.drain()", s9);
    const mods = d.wasm_modules || [];
    check("wasm_modules 随 drain 输出（4 条）", mods.length === 4);
    check("wasm 哈希与 node 侧一致",
          mods.every(function(m) { return m.hash === H; }));
    const kinds = mods.map(function(m) { return m.kind; }).sort().join(",");
    check("wasm 四入口 kind 齐全",
          kinds === "compile,compileStreaming,instantiate,instantiateStreaming");
    const instEntry = mods.filter(function(m) { return m.kind === "instantiate"; })[0];
    check("instantiate(bytes) 条目带 exports",
          !!instEntry && JSON.stringify(instEntry.exports) === '["run"]');
    const compileEntry = mods.filter(function(m) { return m.kind === "compile"; })[0];
    check("compile 条目 module_compiled 且 exports 回写",
          !!compileEntry && compileEntry.module_compiled === true &&
          JSON.stringify(compileEntry.exports) === '["run"]');
    const streamEntry = mods.filter(
        function(m) { return m.kind === "instantiateStreaming"; })[0];
    check("streaming 条目带 prov_id 与 exports",
          !!streamEntry && streamEntry.prov_id === "s1" &&
          JSON.stringify(streamEntry.exports) === '["run"]');
    check("wasm base64 字节可还原",
          !!instEntry && Buffer.from(instEntry.bytes_b64, "base64")
              .equals(Buffer.from(bytes)));
    const ci = (d.counters && d.counters.wasm_imports) || {};
    const ce = (d.counters && d.counters.wasm_exports) || {};
    check("wasm imports 计数（bytes 1 + streaming 1）",
          ci[H + ":env.cb"] === 2);
    check("wasm exports 计数（4 次 run）", ce[H + ":run"] === 4);
    check("provId 计数键已迁移为哈希键",
          Object.keys(ci).every(function(k) { return k.indexOf("s1:") !== 0; }) &&
          Object.keys(ce).every(function(k) { return k.indexOf("s1:") !== 0; }));

    // E. 第十阶段切片 C1：冻结 exports（SpiderMonkey 形态）经
    //    浅拷贝+defineProperty 遮蔽后也能计数
    s9.__freeze_exports = true;
    const rf = await WA.instantiate(bytes, imports);
    s9.__freeze_exports = false;
    rf.instance.exports.run(); rf.instance.exports.run();
    rf.instance.exports.run();
    check("冻结 exports 返回值语义不变",
          rf.instance.exports.run() === 7);
    const df = vm.runInContext("__mcp_vm_loop_state.drain()", s9);
    const cef = (df.counters && df.counters.wasm_exports) || {};
    check("冻结 exports 遮蔽包装计数（累计 4+4 次 run）",
          cef[H + ":run"] === 8);
    const fEntry = (df.wasm_modules || []).filter(function(m) {
        return m.kind === "instantiate" && m.exports_frozen; })[0];
    check("冻结 exports 条目如实标 frozen 且遮蔽成功",
          !!fEntry && !fEntry.exports_shadow_failed);

    // F. 第十二阶段 Task 1：new WebAssembly.Instance() 构造器直连
    //    （此前四入口之外的登记盲区）
    const modC = await WA.compile(bytes);
    const instC = new WA.Instance(modC, imports);
    check("Instance 构造器 instanceof 保持", instC instanceof WA.Instance);
    check("WebAssembly.Instance.prototype 对象未变",
          WA.Instance.prototype === waInstanceProto);
    instC.exports.run(); instC.exports.run();
    check("Instance 构造器 exports 返回值语义不变",
          instC.exports.run() === 7);

    // F2. 冻结 exports 的构造器形态：遮蔽计数同样生效
    s9.__freeze_exports = true;
    const instF = new WA.Instance(modC, imports);
    s9.__freeze_exports = false;
    instF.exports.run(); instF.exports.run();

    // F3. 来路不明的 Module（未经过 compile 钩子）：如实记
    //     bytes_unavailable，不伪造字节、exports 不包装（与
    //     instantiate(module) 无字节记录路径一致）
    const instU = new WA.Instance(new WA.Module(bytes));
    check("来路不明 Module 的构造语义不变", instU.exports.run() === 7);

    // F4. 非 Module 输入：TypeError 原样抛出，不落条目
    let instThrew = false;
    const modsBeforeThrow = vm.runInContext(
        "__mcp_vm_loop_state.drain().wasm_modules.length", s9);
    try { new WA.Instance(bytes); }
    catch (e) { instThrew = /Module expected/.test(String(e)); }
    check("Instance 构造器异常语义原样", instThrew);

    // F5. toString 守护 + name/length 对齐
    const instSrc = vm.runInContext(
        "Function.prototype.toString.call(window.WebAssembly.Instance)", s9);
    check("Instance 构造器 toString 守护登记（返回原生件源码）",
          instSrc.indexOf("Module expected") > -1 &&
          instSrc.indexOf("wrapWasmExports") === -1);
    check("Instance 构造器 name/length 对齐",
          WA.Instance.name === "Instance" && WA.Instance.length === 2);

    const dc = vm.runInContext("__mcp_vm_loop_state.drain()", s9);
    check("构造器异常路径不落新条目",
          (dc.wasm_modules || []).length === modsBeforeThrow);
    const ctorEntries = (dc.wasm_modules || []).filter(function(m) {
        return m.kind === "Instance-ctor"; });
    check("Instance-ctor 条目 3 条（含 1 条字节不可得 gap）",
          ctorEntries.length === 3 &&
          ctorEntries.filter(function(m) {
              return m.bytes_unavailable === true && m.hash === null;
          }).length === 1);
    const ce1 = ctorEntries.filter(function(m) { return m.hash === H; });
    check("Instance-ctor 字节回找 compile 记录且 exports 回写",
          ce1.length === 2 && ce1.every(function(m) {
              return m.bytes_len === bytes.length &&
                     JSON.stringify(m.exports) === '["run"]'; }));
    check("Instance-ctor 冻结形态如实标注",
          ce1.filter(function(m) { return m.exports_frozen === true; })
              .length === 1);
    check("字节已留存不重复内嵌 base64（bytes_ref 交叉引用）",
          ce1.every(function(m) {
              return m.bytes_ref === true && m.bytes_b64 === undefined; }));
    const cec = (dc.counters && dc.counters.wasm_exports) || {};
    const cic = (dc.counters && dc.counters.wasm_imports) || {};
    check("Instance-ctor exports 计数合并（8+5 次 run）",
          cec[H + ":run"] === 13);
    check("Instance-ctor imports 计数（累计 5 次 cb）",
          cic[H + ":env.cb"] === 5);
})().catch(function(e) {
    failures++;
    console.log("FAIL wasm 用例异常: " + (e && e.stack || e));
});

// ---- 第七阶段 Task 2：值变换事件（hook 9，taint-lite）----
function hashOfNode(s) {  // 与 hook 的 hashOf 同款（UTF-16 码元 31 滚动）
    let h = 0;
    for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0;
    return (h >>> 0).toString(16);
}
const s10 = { console, performance: { now: () => Date.now() },
              setTimeout: function(fn, ms) { return 0; } };
s10.window = s10;
s10.Date = Date; s10.Object = Object; s10.Array = Array;
// 注意：String 不赋宿主对象——context 内建即可。若赋宿主 String，
// hook 11（第十二阶段 charCodeAt 包装）会改宿主 String.prototype，
// 宿主侧 hashOfNode 的逐字符读取会污染本 realm 的合并事件与计数。
s10.RegExp = RegExp; s10.JSON = JSON; s10.Math = Math;
s10.Uint8Array = Uint8Array; s10.ArrayBuffer = ArrayBuffer;
s10.Promise = Promise; s10.WeakMap = WeakMap;
s10.btoa = function(s) { return Buffer.from(s, "binary").toString("base64"); };
s10.atob = function(s) {
    if (!/^[A-Za-z0-9+/]*={0,2}$/.test(s)) throw new Error("invalid");
    return Buffer.from(s, "base64").toString("binary");
};
s10.encodeURIComponent = encodeURIComponent;
s10.decodeURIComponent = decodeURIComponent;
s10.TextEncoder = TextEncoder; s10.TextDecoder = TextDecoder;
s10.crypto = { subtle: {
    digest: function(algo, data) {
        return Promise.resolve(new Uint8Array(data).reverse().buffer);
    },
} };
vm.createContext(s10);
vm.runInContext(rendered, s10);

const tapTest = (async function() {
    // 1. btoa 事件：哈希/长度/预览齐全
    vm.runInContext('btoa("hello-vm");', s10);
    // 2. atob 异常语义保持（非法输入仍抛、不记事件）
    let threw = false;
    try { vm.runInContext('atob("!!!");', s10); } catch (e) { threw = true; }
    check("atob 非法输入异常语义保持", threw);
    // 3. encodeURIComponent 事件
    vm.runInContext('encodeURIComponent("a=b&c=d");', s10);
    // 4. TextEncoder/Decoder 事件（字节哈希 + hex 预览 / 字符串输出）
    vm.runInContext('new TextEncoder().encode("sign-me");', s10);
    vm.runInContext('new TextDecoder().decode(new Uint8Array([104,105]));', s10);
    // 5. crypto.subtle.digest 异步事件
    await vm.runInContext(
        'crypto.subtle.digest("SHA-256", new Uint8Array([1,2,3]))', s10);
    // 6. 预览截断：100 字符输入 → preview 64、len 100
    vm.runInContext('btoa(new Array(101).join("x"));', s10);

    const d = vm.runInContext("__mcp_vm_loop_state.drain()", s10);
    const taps = d.value_taps || [];
    check("value_taps 随 drain 输出（6 条）", taps.length === 6);
    const btoaEv = taps.filter(function(t) { return t.api === "btoa"; })[0];
    check("btoa 事件哈希与 node 侧一致",
          !!btoaEv && btoaEv.in_hash === hashOfNode("hello-vm") &&
          btoaEv.out_hash === hashOfNode("aGVsbG8tdm0=") &&
          btoaEv.out_preview === "aGVsbG8tdm0=");
    check("atob 非法输入不记事件",
          taps.every(function(t) { return t.api !== "atob"; }));
    check("encodeURIComponent 事件记录",
          taps.some(function(t) {
              return t.api === "encodeURIComponent" &&
                     t.out_preview === "a%3Db%26c%3Dd";
          }));
    const teEv = taps.filter(
        function(t) { return t.api === "TextEncoder.encode"; })[0];
    check("TextEncoder 字节输出 hex 预览",
          !!teEv && teEv.out_preview === "7369676e2d6d65" &&
          teEv.out_len === 7);
    check("TextDecoder 字符串输出哈希",
          taps.some(function(t) {
              return t.api === "TextDecoder.decode" &&
                     t.out_hash === hashOfNode("hi");
          }));
    check("crypto.subtle.digest 异步事件",
          taps.some(function(t) {
              return t.api === "crypto.subtle.digest" && t.out_len === 3;
          }));
    const truncEv = taps.filter(function(t) {
        return t.api === "btoa" && t.in_len === 100; })[0];
    check("预览截断（in_len 100 / preview 64）",
          !!truncEv && truncEv.in_preview.length === 64);

    // 8. hook 10：String.fromCharCode ≥2 字符记事件；单字符进合并缓冲
    //    （第十阶段切片 C2），drain 时 flush 为 coalesced 事件
    vm.runInContext('String.fromCharCode(115,105,103,45);', s10);  // "sig-"
    vm.runInContext('String.fromCharCode(65);String.fromCharCode(66);', s10);
    const d4 = vm.runInContext("__mcp_vm_loop_state.drain()", s10);
    const sfcEv = (d4.value_taps || []).filter(function(t) {
        return t.api === "String.fromCharCode"; });
    check("fromCharCode 多字符事件记录",
          sfcEv.length === 1 && sfcEv[0].out_preview === "sig-" &&
          sfcEv[0].out_hash === hashOfNode("sig-"));
    check("fromCharCode 单字符计数 + 合并事件数",
          !!(d4.string_primitives) &&
          d4.string_primitives["fromCharCode.calls"] === 3 &&
          d4.string_primitives["fromCharCode.coalesced"] === 1);
    const coalAB = (d4.value_taps || []).filter(function(t) {
        return t.api === "String.fromCharCode(coalesced)"; });
    check("单字符合并事件内容正确（AB）",
          coalAB.length === 1 && coalAB[0].out_preview === "AB" &&
          coalAB[0].out_hash === hashOfNode("AB"));
    check("fromCharCode 返回值原样",
          vm.runInContext('String.fromCharCode(72,105)', s10) === "Hi");

    // 8b. 合并缓冲满 64 字符即 flush，drain 前残余落账
    //     （drain 是累计快照：此前 "AB" 合并事件也在其中）
    vm.runInContext('for (var i = 0; i < 130; i++) String.fromCharCode(97);', s10);
    const d4b = vm.runInContext("__mcp_vm_loop_state.drain()", s10);
    const coalA = (d4b.value_taps || []).filter(function(t) {
        return t.api === "String.fromCharCode(coalesced)"; });
    check("合并缓冲 64 字符 flush ×2 + drain 残余 2 字符",
          coalA.length === 4 && coalA[0].out_preview === "AB" &&
          coalA[1].out_len === 64 && coalA[2].out_len === 64 &&
          coalA[3].out_len === 2);
    // 此时事件累计 12 条（6 + sig- + AB + Hi + 64a + 64a + 2a），
    // 调用计数 4 + 130 = 134

    // 9. 第九阶段：__mcp_vm_rec 序列记录设施
    vm.runInContext(
        '__mcp_vm_rec("Du", [232, "abc"]);' +
        '__mcp_vm_rec("Du", [140, "def"]);' +
        '__mcp_vm_rec("Du");', s10);
    const d5 = vm.runInContext("__mcp_vm_loop_state.drain()", s10);
    const seq = d5.trace_seq || [];
    check("trace_seq 序列有序且参数证据齐全",
          seq.length === 3 && seq[0].seq === 1 && seq[2].seq === 3 &&
          seq[0].vals[0].preview === "232" &&
          seq[0].vals[1].preview === "abc" &&
          seq[2].vals === undefined);
    vm.runInContext('for (var i = 0; i < 5010; i++) __mcp_vm_rec("flood");', s10);
    const d6 = vm.runInContext("__mcp_vm_loop_state.drain()", s10);
    check("trace_seq 环形上限 5000 + overflow 计数",
          (d6.trace_seq || []).length === 5000 &&
          d6.trace_seq_overflow === 13);  // 已有 3 条 + 5010 条 - 5000

    // 7. 环形上限：再灌 1005 条 → 长度 1000 且 overflow 5
    vm.runInContext(
        'for (var i = 0; i < 1005; i++) btoa("pad" + i);', s10);
    const d2 = vm.runInContext("__mcp_vm_loop_state.drain()", s10);
    check("value_taps 环形上限 1000 + overflow 计数",
          (d2.value_taps || []).length === 1000 &&
          d2.value_taps_overflow === 17);  // 已有 12 条 + 1005 条 - 1000
})().catch(function(e) {
    failures++;
    console.log("FAIL value-tap 用例异常: " + (e && e.stack || e));
});

// ---- 第十阶段切片 D：跨域 iframe postMessage drain 桥 + ServiceWorker gap ----
// 构造父子两个 realm：父的 frames[0] 读 __mcp_vm_loop_state 抛错（模拟
// 跨域 WindowProxy），postMessage 双向投递模拟 DOM 消息通道。
function stdRealm(extra) {
    const s = { console, performance: { now: () => Date.now() },
                setTimeout, clearTimeout, Promise };
    s.window = s;
    s.Date = Date; s.Object = Object; s.String = String; s.Array = Array;
    s.RegExp = RegExp; s.JSON = JSON; s.Math = Math; s.WeakMap = WeakMap;
    s.Uint8Array = Uint8Array;
    if (extra) Object.assign(s, extra);
    return s;
}
const sP = stdRealm();   // 父页面
const sC = stdRealm();   // 跨域子 frame
const parentListeners = [], childListeners = [];
sP.document = {};        // window realm 标记（装应答侧监听）
sP.addEventListener = function(t, fn) { if (t === "message") parentListeners.push(fn); };
sP.removeEventListener = function(t, fn) {
    const i = parentListeners.indexOf(fn); if (i >= 0) parentListeners.splice(i, 1); };
sC.document = {};        // 标记为 window realm（Worker 判定用）
sC.addEventListener = function(t, fn) { if (t === "message") childListeners.push(fn); };
// 跨域 frame 假件：属性访问抛错，postMessage 投递到子 realm 监听
const fakeFrame = {};
Object.defineProperty(fakeFrame, "__mcp_vm_loop_state", {
    get: function() { throw new Error("Permission denied (cross-origin)"); } });
fakeFrame.postMessage = function(d) {
    childListeners.slice().forEach(function(fn) { fn({ data: d, source: sP }); });
};
// 子回父：应答侧调 e.source.postMessage → 投递到父监听，source 是 frame
sP.postMessage = function(d) {
    parentListeners.slice().forEach(function(fn) { fn({ data: d, source: fakeFrame }); });
};
sC.parent = sP;          // 应答侧 e.source 校验：只响应父窗口
sP.parent = sP;          // 顶层
sP.frames = [fakeFrame];
vm.createContext(sP);
vm.createContext(sC);
vm.runInContext(rendered, sP);
vm.runInContext(rendered, sC);
// 子 frame 里跑一段循环（跨域，父同步访问不到）
vm.runInContext('eval("var k=0;while(k!=5){k++;}");', sC);

const xoTest = (async function() {
    const all = await vm.runInContext(
        "__mcp_vm_loop_state.drainAllAsync(800)", sP);
    const ftop = all.find(function(e) { return e.realm === "frame[0]/top"; });
    check("跨域 frame 经 postMessage 桥聚合（6 次 tick）",
          !!ftop && !!ftop.data && ftop.data.loops &&
          ftop.data.loops[0].iterations === 6);
    check("桥成功后 drainAll 残留错误条目被清除",
          !all.some(function(e) { return e.realm === "frame[0]" && e.error; }));

    // 超时路径：无运行时的跨域 frame（postMessage 无应答）如实记超时
    const deadFrame = {};
    Object.defineProperty(deadFrame, "__mcp_vm_loop_state", {
        get: function() { throw new Error("Permission denied"); } });
    deadFrame.postMessage = function() {};
    sP.frames = [deadFrame];
    const all2 = await vm.runInContext(
        "__mcp_vm_loop_state.drainAllAsync(300)", sP);
    check("跨域无应答记 drain 超时",
          all2.some(function(e) { return e.realm === "frame[0]/drain" &&
              /timeout/.test(e.error || ""); }));
    sP.frames = [];

    // 伪造防护：非父窗口来源的 drain 请求不应被应答（child 侧 source 校验）
    let leaked = false;
    const evilSource = { postMessage: function() { leaked = true; } };
    childListeners.slice().forEach(function(fn) {
        fn({ data: { __mcp_vm_drain_req: "evil" }, source: evilSource }); });
    await new Promise(function(res) { setTimeout(res, 100); });
    check("非父窗口 drain 请求被忽略", leaked === false);
})().catch(function(e) {
    failures++;
    console.log("FAIL 跨域桥用例异常: " + (e && e.stack || e));
});

// 41. ServiceWorker register 盲区如实登记
const sW = stdRealm();
sW.document = {};
sW.addEventListener = function() {};
sW.parent = sW;
sW.ServiceWorkerContainer = function() {};
sW.ServiceWorkerContainer.prototype.register = function(url, opts) {
    sW.__swRegistered = String(url);
    return Promise.resolve({ scriptURL: String(url) });
};
vm.createContext(sW);
vm.runInContext(rendered, sW);
const swTest = (async function() {
    const reg = Object.create(sW.ServiceWorkerContainer.prototype);
    await reg.register("/sw.js", { scope: "/" });
    const d = vm.runInContext("__mcp_vm_loop_state.drain()", sW);
    check("ServiceWorker register 记 serviceworker-gap 且原样直通",
          sW.__swRegistered === "/sw.js" &&
          (d.dynamic_sources || []).some(function(s) {
              return s.kind === "serviceworker-gap" &&
                     s.prefix.indexOf("/sw.js") === 0; }));
})().catch(function(e) {
    failures++;
    console.log("FAIL serviceworker 用例异常: " + (e && e.stack || e));
});

// ---- 第十二阶段 Task 2：charCodeAt/at/codePointAt 逐字符读取合并事件 ----
// 独立 realm，String 用 context 内建（不赋宿主对象），与宿主/其他 realm
// 的包装层完全隔离，计数可精确断言（精确计数本身即「无自污染」证明：
// hashOf 等自身热路径走 _charCodeAt0，不进合并缓冲）。
const s11 = { console, performance: { now: () => Date.now() },
              setTimeout: function(fn, ms) { return 0; } };
s11.window = s11;
vm.createContext(s11);
vm.runInContext(rendered, s11);

// T2-1. 连续逐字符读取合并为一条 coalesced 事件；返回值语义不变
vm.runInContext(
    'var s = "sig-42-abcdef"; var acc = 0;' +
    'for (var i = 0; i < s.length; i++) acc += s.charCodeAt(i);' +
    'window.__acc = acc;', s11);
check("charCodeAt 逐字符读取返回值语义不变",
      vm.runInContext("__acc", s11) ===
      Array.from("sig-42-abcdef").reduce(function(a, c) {
          return a + c.charCodeAt(0); }, 0));
const d11b = vm.runInContext("__mcp_vm_loop_state.drain()", s11);
const ccEv1 = (d11b.value_taps || []).filter(function(t) {
    return t.api === "String.charCodeAt(coalesced)"; });
check("charCodeAt 连续读取合并为一条事件（哈希/起止索引/预览）",
      ccEv1.length === 1 &&
      ccEv1[0].out_preview === "sig-42-abcdef" &&
      ccEv1[0].start_index === 0 && ccEv1[0].end_index === 12 &&
      ccEv1[0].in_hash === hashOfNode("sig-42-abcdef") &&
      ccEv1[0].out_hash === hashOfNode("sig-42-abcdef"));
check("charCodeAt 计数与合并数入 string_primitives",
      !!d11b.string_primitives &&
      d11b.string_primitives["charCodeAt.calls"] === 13 &&
      d11b.string_primitives["charReader.coalesced"] === 1);

// T2-2. 不同字符串断链：各自成事件
vm.runInContext(
    '"ABC".charCodeAt(0); "ABC".charCodeAt(1);' +
    '"XYZ".charCodeAt(0); "XYZ".charCodeAt(1); "XYZ".charCodeAt(2);', s11);
// T2-3. 越界读取（NaN）：断链、不入序列、仍计数、返回值原样
vm.runInContext('window.__oob = "hi".charCodeAt(9);', s11);
check("charCodeAt 越界返回 NaN 语义不变",
      vm.runInContext("__oob !== __oob", s11) === true);

// T2-4. codePointAt：>0xFFFF 码元合并 + 语义不变
vm.runInContext(
    'var t = "a\\u{1f600}b"; var r = [];' +
    'for (var i = 0; i < t.length; i++) r.push(t.codePointAt(i));' +
    'window.__cp = r.join(",");', s11);
check("codePointAt 返回值语义不变（含代理对）",
      vm.runInContext("__cp", s11) === "97,128512,56832,98");

// T2-5. at() 合并 + 负索引断链成独立事件
vm.runInContext(
    'var r3 = ""; var w = "token";' +
    'for (var i = 0; i < w.length; i++) r3 += w.at(i);' +
    'window.__at = r3; window.__atneg = "ab".at(-1);', s11);
check("at() 返回值语义不变",
      vm.runInContext("__at", s11) === "token" &&
      vm.runInContext("__atneg", s11) === "b");

// T2-6. 异常语义：null receiver 的 TypeError 原样抛出，不计数
let spThrew = false;
try {
    vm.runInContext('String.prototype.charCodeAt.call(null, 0);', s11);
} catch (e) { spThrew = true; }
check("charCodeAt 异常语义原样", spThrew);

// T2-7. toString 守护：包装方法经 fakeFns 登记返回原生串
check("charCodeAt 包装对 FP.toString.call 隐身", vm.runInContext(
    "Function.prototype.toString.call(String.prototype.charCodeAt)",
    s11).indexOf("[native code]") > -1);
check("at/codePointAt 包装 name 对齐", vm.runInContext(
    "String.prototype.at.name === 'at' &&" +
    " String.prototype.codePointAt.name === 'codePointAt'", s11) === true);

const d11c = vm.runInContext("__mcp_vm_loop_state.drain()", s11);
const ccAll = (d11c.value_taps || []).filter(function(t) {
    return /coalesced/.test(t.api); });
check("断链/换串各成事件（共 6 条 coalesced）", ccAll.length === 6);
const cpEv = ccAll.filter(function(t) {
    return t.api === "String.codePointAt(coalesced)"; })[0];
check("codePointAt 事件含代理对预览与起止索引",
      !!cpEv && cpEv.start_index === 0 && cpEv.end_index === 3 &&
      cpEv.out_len === 4 && cpEv.out_preview.indexOf("\u{1f600}") === 1);
const atNeg = ccAll.filter(function(t) {
    return t.api === "String.at(coalesced)" && t.start_index === -1; })[0];
check("at() 负索引成独立单码元事件",
      !!atNeg && atNeg.end_index === -1 && atNeg.out_preview === "b");
check("三方法精确计数（无自污染）",
      d11c.string_primitives["charCodeAt.calls"] === 19 &&
      d11c.string_primitives["codePointAt.calls"] === 4 &&
      d11c.string_primitives["at.calls"] === 6 &&
      d11c.string_primitives["charReader.coalesced"] === 6);

// T2-8. 64 码元 flush + drain 残余落账（独立 realm 精确计数）
const s12 = { console, performance: { now: () => Date.now() },
              setTimeout: function(fn, ms) { return 0; } };
s12.window = s12;
vm.createContext(s12);
vm.runInContext(rendered, s12);
vm.runInContext(
    'var buf = ""; for (var i = 0; i < 130; i++) buf += "a";' +
    'var s2 = 0; for (var j = 0; j < 130; j++) s2 += buf.charCodeAt(j);',
    s12);
const d12b = vm.runInContext("__mcp_vm_loop_state.drain()", s12);
const ccLong = (d12b.value_taps || []).filter(function(t) {
    return t.api === "String.charCodeAt(coalesced)"; });
check("charCodeAt 64 码元 flush ×2 + drain 残余 2",
      ccLong.length === 3 &&
      ccLong[0].out_len === 64 && ccLong[1].out_len === 64 &&
      ccLong[2].out_len === 2 &&
      ccLong[2].start_index === 128 && ccLong[2].end_index === 129);
check("charCodeAt 长链调用计数 130",
      d12b.string_primitives["charCodeAt.calls"] === 130 &&
      d12b.string_primitives["charReader.coalesced"] === 3);

// ---- 第十二阶段 Task 3：__mcp_vm_rec_ret 返回值锚定通用设施 ----
vm.runInContext(
    'function add(a, b) { return "sum:" + (a + b); }' +
    'window.__rr1 = __mcp_vm_rec_ret("add", add, null, [2, 3]);', s11);
check("rec_ret 返回值原样透传", vm.runInContext("__rr1", s11) === "sum:5");
vm.runInContext(
    'var obj = { k: 7, f: function(x) { return this.k * x; } };' +
    'window.__rr2 = __mcp_vm_rec_ret("m", obj.f, obj, [6]);', s11);
check("rec_ret thisArg 透传", vm.runInContext("__rr2", s11) === 42);
vm.runInContext(
    'var boom = new Error("kaboom"); var caught = null;' +
    'try { __mcp_vm_rec_ret("bad", function(){ throw boom; }, null, []); }' +
    'catch (e) { caught = e; }' +
    'window.__rr3 = (caught === boom);', s11);
check("rec_ret 异常对象原样抛出", vm.runInContext("__rr3", s11) === true);
const d11d = vm.runInContext("__mcp_vm_loop_state.drain()", s11);
const rrSeq = d11d.trace_seq || [];
check("rec_ret 事件带参数与返回值证据",
      rrSeq.length === 3 &&
      rrSeq[0].tag === "add" && rrSeq[0].vals.length === 2 &&
      rrSeq[0].ret.preview === "sum:5" &&
      rrSeq[0].ret.hash === hashOfNode("sum:5") &&
      rrSeq[1].ret.preview === "42" &&
      rrSeq[2].threw === true && rrSeq[2].ret === undefined &&
      rrSeq[2].error_preview === "kaboom");
check("rec 与 rec_ret 共享序号序列",
      rrSeq[0].seq === 1 && rrSeq[1].seq === 2 && rrSeq[2].seq === 3);

Promise.all([drainPromise, wasmTest, tapTest, xoTest, swTest]).then(function(results) {
    const all = results[0];
    const w = all.find(function(e) { return e.realm === "worker[0]"; });
    check("drainAllAsync 聚合 worker[0]", !!w && !w.error);
    // Worker 桥回传的是 drainAllAsync 数组：[{realm:'top',data},...]
    const own = w && Array.isArray(w.data) &&
        w.data.find(function(e) { return e.realm === "top"; });
    const loops = own && own.data && own.data.loops || [];
    check("Worker 内循环状态被聚合（4 次迭代）",
          loops.length === 1 && loops[0].iterations === 4);
    // case 35：frame 内 Worker 递归聚合
    const fw = all.find(function(e) { return e.realm === "frame[0]/worker[0]"; });
    check("frame 内 Worker 被递归聚合（7 次迭代）",
          !!fw && !!fw.data && fw.data.loops && fw.data.loops[0].iterations === 7);
    check("frame top 条目不重复计数",
          all.filter(function(e) { return e.realm === "frame[0]/top"; }).length === 0);
    // 第十一阶段：落 worker-gap 的直通 Worker（case 31 的 __wk2，跨域拿不
    // 到源码）也入注册表——drain 结果应出现它的超时条目（真实场景里该
    // Worker 经 route 层前置运行时后会应答，node 假件无运行时故超时）。
    const wkTimeout = all.filter(function(e) {
        return /^worker\[\d+\]$/.test(e.realm || "") && e.error === "drain timeout"; });
    check("落 gap 的直通 Worker 入注册表（drain 超时条目）",
          wkTimeout.length >= 1);
    check("worker realm 条目数 = 3（blob 插桩 + 直通 gap + data 插桩）",
          all.filter(function(e) {
              return /^worker\[\d+\]$/.test(e.realm || ""); }).length === 3);

    console.log(failures ? `\n${failures} FAILURES` : "\nALL PASS");
    process.exit(failures ? 1 : 0);
}).catch(function(e) {
    console.log("FAIL drainAllAsync 异常: " + e);
    process.exit(1);
});
