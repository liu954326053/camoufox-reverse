if (window.__mcp_install_raw_network) window.__mcp_install_raw_network('xhr');
(function() {
    if (window.__mcp_xhr_shape_hooked_v3) return;
    window.__mcp_xhr_shape_hooked_v3 = true;
    window.__mcp_xhr_shape_log = window.__mcp_xhr_shape_log || [];

    const proto = XMLHttpRequest.prototype;
    const _open = proto.open;
    const _send = proto.send;
    const _setRequestHeader = proto.setRequestHeader;
    const MAX_DEPTH = 8;
    const MAX_ITEMS = 80;
    const MAX_PARSE_LENGTH = 200000;

    // toString 伪装守护（第十一阶段）：vm_loop 运行时不在场时自立门户，
    // 与 fetch_hook 共用 __mcp_fake_fn_src 登记表。
    var _FPT = Function.prototype.toString;
    var fakeSrc = window.__mcp_fake_fn_src;
    if (!fakeSrc && typeof WeakMap !== 'undefined') {
        fakeSrc = new WeakMap();
        window.__mcp_fake_fn_src = fakeSrc;
        // 不用自有标记属性做重复安装检测（可被 getOwnPropertyNames
        // 枚举），直接查 WeakMap。
        if (!fakeSrc.has(Function.prototype.toString)) {
            var guarded = function() {
                try { if (fakeSrc.has(this)) return fakeSrc.get(this); } catch (e) {}
                return _FPT.call(this);
            };
            try { guarded.prototype = undefined; } catch (e) {}
            Object.defineProperty(guarded, 'name',
                { value: 'toString', configurable: true });
            var selfSrc;
            try { selfSrc = _FPT.call(_FPT); } catch (e) {
                selfSrc = 'function toString() { [native code] }';
            }
            fakeSrc.set(guarded, selfSrc);
            Function.prototype.toString = guarded;
        }
    }
    var regFake = function(fake, orig) {
        // name/length 与原函数对齐（反爬指纹校验常见），再登记源码串；
        // prototype 值对齐 undefined（原生非构造器函数无 prototype）
        try {
            Object.defineProperty(fake, 'name',
                { value: orig.name, configurable: true });
            Object.defineProperty(fake, 'length',
                { value: orig.length, configurable: true });
            if (!orig.hasOwnProperty('prototype') &&
                fake.hasOwnProperty('prototype')) fake.prototype = undefined;
        } catch (e) {}
        if (!fakeSrc) return;
        var src;
        try { src = _FPT.call(orig); } catch (e) { return; }
        try { fakeSrc.set(fake, src); } catch (e) {}
    };

    const safeUrl = function(input) {
        try {
            const parsed = new URL(String(input), location.href);
            const queryKeys = [];
            parsed.searchParams.forEach(function(_value, key) {
                if (!queryKeys.includes(key)) queryKeys.push(key);
            });
            queryKeys.sort();
            const safeQuery = {};
            for (const key of ['rpcids', 'rt']) {
                const value = parsed.searchParams.get(key);
                if (value && /^[A-Za-z0-9,_-]{1,100}$/.test(value)) safeQuery[key] = value;
            }
            return { origin: parsed.origin, pathname: parsed.pathname, queryKeys, safeQuery };
        } catch (e) {
            return { type: 'unparseable-url', length: String(input).length };
        }
    };

    const summarizeString = function(value, depth) {
        const text = String(value);
        const trimmed = text.trim().replace(/^\)\]\}'\s*/, '');
        if (depth < MAX_DEPTH && text.length <= MAX_PARSE_LENGTH &&
            (trimmed.startsWith('[') || trimmed.startsWith('{'))) {
            try {
                return {
                    type: 'json-string',
                    length: text.length,
                    value: summarize(JSON.parse(trimmed), depth + 1)
                };
            } catch (e) {}
        }
        return { type: 'string', length: text.length };
    };

    const summarize = function(value, depth) {
        depth = depth || 0;
        if (depth > MAX_DEPTH) return { type: 'depth-limit' };
        if (value === null) return null;
        if (Array.isArray(value)) {
            return {
                type: 'array',
                length: value.length,
                items: value.slice(0, MAX_ITEMS).map(item => summarize(item, depth + 1)),
                truncated: value.length > MAX_ITEMS
            };
        }
        if (typeof value === 'string') return summarizeString(value, depth);
        if (typeof value === 'number' || typeof value === 'boolean') return value;
        if (typeof value === 'undefined') return { type: 'undefined' };
        if (value instanceof URLSearchParams) return summarizeForm(value, depth + 1);
        if (typeof FormData !== 'undefined' && value instanceof FormData) {
            const fields = {};
            value.forEach(function(item, key) {
                if (!fields[key]) fields[key] = [];
                fields[key].push(summarize(item, depth + 1));
            });
            return { type: 'form-data', fields };
        }
        if (typeof Blob !== 'undefined' && value instanceof Blob) {
            return { type: 'blob', size: value.size, mimeType: value.type || '' };
        }
        if (value instanceof ArrayBuffer) return { type: 'array-buffer', byteLength: value.byteLength };
        if (ArrayBuffer.isView(value)) return { type: 'typed-array', byteLength: value.byteLength };
        if (typeof value === 'object') {
            const allKeys = Object.keys(value);
            const fields = {};
            for (const key of allKeys.slice(0, MAX_ITEMS)) fields[key] = summarize(value[key], depth + 1);
            return { type: 'object', keys: allKeys.length, fields, truncated: allKeys.length > MAX_ITEMS };
        }
        return { type: typeof value };
    };

    const summarizeForm = function(params, depth) {
        const fields = {};
        params.forEach(function(value, key) {
            if (!fields[key]) fields[key] = [];
            fields[key].push(summarizeString(value, depth + 1));
        });
        return { type: 'urlencoded-form', fields };
    };

    const summarizeBody = function(body) {
        if (body === null || typeof body === 'undefined') return null;
        if (typeof body === 'string') {
            try { if (body.includes('=')) return summarizeForm(new URLSearchParams(body), 0); }
            catch (e) {}
        }
        return summarize(body, 0);
    };

    const summarizeResponseText = function(text) {
        if (typeof text !== 'string') return { type: typeof text };
        if (text.length > MAX_PARSE_LENGTH) return { type: 'string', length: text.length, oversized: true };
        const trimmed = text.trim().replace(/^\)\]\}'\s*/, '');
        try {
            return { type: 'json-response', length: text.length, value: summarize(JSON.parse(trimmed), 0) };
        } catch (e) {}
        const lines = trimmed.split(/\r?\n/).filter(Boolean);
        const parsedLines = [];
        for (const line of lines.slice(0, MAX_ITEMS)) {
            if (!(line.startsWith('[') || line.startsWith('{'))) continue;
            try { parsedLines.push(summarize(JSON.parse(line), 0)); } catch (e) {}
        }
        return parsedLines.length ?
            { type: 'json-lines-response', length: text.length, lines: parsedLines } :
            { type: 'string', length: text.length };
    };

    const push = function(entry) {
        if (entry.__logged) return;
        entry.__logged = true;
        window.__mcp_xhr_shape_log.push(entry);
        if (window.__mcp_xhr_shape_log.length > 200) window.__mcp_xhr_shape_log.shift();
        console.log('CODEX_SAFE_XHR_SHAPE:' + JSON.stringify(entry));
    };

    const hookedOpen = function(method, url) {
        this.__mcp_shape = {
            method: String(method || 'GET'),
            url: safeUrl(url),
            headerNames: [],
            timestamp: Date.now()
        };
        return _open.apply(this, arguments);
    };

    const hookedSetRequestHeader = function(name, value) {
        if (this.__mcp_shape) {
            const normalized = String(name).toLowerCase();
            if (!this.__mcp_shape.headerNames.includes(normalized)) {
                this.__mcp_shape.headerNames.push(normalized);
                this.__mcp_shape.headerNames.sort();
            }
        }
        return _setRequestHeader.apply(this, arguments);
    };

    const hookedSend = function(body) {
        const entry = this.__mcp_shape;
        if (entry) {
            entry.body = summarizeBody(body);
            this.addEventListener('loadend', function() {
                entry.status = this.status;
                entry.responseUrl = safeUrl(this.responseURL || '');
                try {
                    if (!this.responseType || this.responseType === 'text') {
                        entry.response = summarizeResponseText(this.responseText || '');
                    } else {
                        entry.response = { type: String(this.responseType) };
                    }
                } catch (e) {
                    entry.response = { type: 'unavailable' };
                }
                push(entry);
            }, { once: true });
        }
        return _send.apply(this, arguments);
    };

    // 第十一阶段（gsxt 加速乐实测）：登记进 toString 守护
    // （Function.prototype.toString.call(fn) 会绕过自有 toString 属性）；
    // 且包装必须 configurable/writable——锁死会让目标自己的 XHR 包装
    // （waf 遥测上报）defineProperty 抛 TypeError，挑战流程直接卡死。
    regFake(hookedOpen, _open);
    regFake(hookedSetRequestHeader, _setRequestHeader);
    regFake(hookedSend, _send);

    document.addEventListener('submit', function(event) {
        const form = event.target;
        if (!(form instanceof HTMLFormElement)) return;
        let body = null;
        try { body = summarize(new FormData(form), 0); } catch (e) {}
        console.log('CODEX_SAFE_FORM_SUBMIT:' + JSON.stringify({
            method: String(form.method || 'GET').toUpperCase(),
            action: safeUrl(form.action || location.href),
            body,
            timestamp: Date.now()
        }));
    }, true);

    try {
        Object.defineProperty(proto, 'open', { value: hookedOpen, writable: true, configurable: true });
        Object.defineProperty(proto, 'setRequestHeader', { value: hookedSetRequestHeader, writable: true, configurable: true });
        Object.defineProperty(proto, 'send', { value: hookedSend, writable: true, configurable: true });
    } catch (e) {
        proto.open = hookedOpen;
        proto.setRequestHeader = hookedSetRequestHeader;
        proto.send = hookedSend;
    }
})();
