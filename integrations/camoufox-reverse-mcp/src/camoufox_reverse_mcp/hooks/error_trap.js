// error_trap.js - 未捕获异常/拒绝 Promise 捕获（第十一阶段新增）
// 反爬目标在插桩下抛出的异常（如 gsxt 加速乐的 RangeError）经 console
// 只能看到消息，没有调用栈。本预设把 window.onerror / unhandledrejection
// 的完整 stack 收进 window.__mcp_error_log，供 evaluate_js 读回。
(function() {
    if (window.__mcp_error_trap) return;
    window.__mcp_error_trap = true;
    window.__mcp_error_log = window.__mcp_error_log || [];
    var MAX = 100;
    function push(kind, msg, src, line, col, stack) {
        try {
            if (window.__mcp_error_log.length >= MAX) window.__mcp_error_log.shift();
            window.__mcp_error_log.push({
                kind: kind,
                message: String(msg).slice(0, 300),
                source: String(src || '').slice(0, 200),
                line: line, col: col,
                stack: String(stack || '').slice(0, 2000),
                ts: Date.now()
            });
        } catch (e) {}
    }
    window.addEventListener('error', function(e) {
        push('error', e.message, e.filename, e.lineno, e.colno,
             e.error && e.error.stack);
    }, true);
    window.addEventListener('unhandledrejection', function(e) {
        var r = e.reason;
        push('unhandledrejection', r && r.message || r, '', 0, 0,
             r && r.stack);
    }, true);
})();
