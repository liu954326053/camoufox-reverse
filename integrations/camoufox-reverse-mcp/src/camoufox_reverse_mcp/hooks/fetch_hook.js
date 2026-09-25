if (window.__mcp_install_raw_network) window.__mcp_install_raw_network('fetch');
(function() {
    if (window.__mcp_fetch_shape_hooked_v3) return;
    window.__mcp_fetch_shape_hooked_v3 = true;
    window.__mcp_fetch_shape_log = window.__mcp_fetch_shape_log || [];

    // toString 伪装守护（第十一阶段）：vm_loop 运行时不在场时自立门户——
    // 反爬 VM 用 Function.prototype.toString.call(fn) 识别包装（绕过自有
    // toString 属性），守护缺失会被加速乐类挑战标记为改机，遥测 405 死循环。
    var _FPT = Function.prototype.toString;
    var fakeSrc = window.__mcp_fake_fn_src;
    if (!fakeSrc && typeof WeakMap !== 'undefined') {
        fakeSrc = new WeakMap();
        window.__mcp_fake_fn_src = fakeSrc;
        // 重复安装检测：不用自有标记属性（getOwnPropertyNames 可枚举
        // 检测面），直接查 WeakMap 里有没有登记当前 toString。
        if (!fakeSrc.has(Function.prototype.toString)) {
            var guarded = function() {
                try { if (fakeSrc.has(this)) return fakeSrc.get(this); } catch (e) {}
                return _FPT.call(this);
            };
            // 普通函数自带 prototype 自有属性（不可配置删不掉），值对齐
            // undefined（原生 toString 无 prototype）；name 对齐；length
            // 默认 0 已一致。Proxy 方案已实测否决（SpiderMonkey 主线程卡死）。
            try { guarded.prototype = undefined; } catch (e) {}
            Object.defineProperty(guarded, 'name',
                { value: 'toString', configurable: true });
            // 自描述串必须取自引擎真实输出（Firefox 原生格式带换行
            // 缩进，与 Chrome 单行格式不同），硬编码会被引擎指纹校验识别。
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
        // prototype 值对齐 undefined（原生非构造器函数无 prototype），
        // 不可配置的自有属性删不掉，只能对齐值。
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

    const _fetch = window.fetch;
    const MAX_DEPTH = 8;
    const MAX_ITEMS = 80;
    const MAX_PARSE_LENGTH = 200000;

    const safeUrl = function(input) {
        try {
            const raw = typeof input === 'string' ? input :
                (input instanceof Request ? input.url : String(input));
            const parsed = new URL(raw, location.href);
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

    const headerNames = function(headers) {
        try {
            const names = [];
            new Headers(headers || {}).forEach(function(_value, key) { names.push(key); });
            return names.sort();
        } catch (e) { return []; }
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

    try {
        const hashFields = {};
        new URLSearchParams(location.hash.replace(/^#/, '')).forEach(function(value, key) {
            if (!hashFields[key]) hashFields[key] = [];
            hashFields[key].push({ type: 'string', length: String(value).length });
        });
        console.log('CODEX_SAFE_LOCATION:' + JSON.stringify({
            origin: location.origin,
            pathname: location.pathname,
            queryKeys: safeUrl(location.href).queryKeys || [],
            hashFields
        }));
    } catch (e) {}

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
        window.__mcp_fetch_shape_log.push(entry);
        if (window.__mcp_fetch_shape_log.length > 200) window.__mcp_fetch_shape_log.shift();
        console.log('CODEX_SAFE_FETCH_REQUEST:' + JSON.stringify(entry));
    };

    // 不用 async 包装（gsxt 实测卡死根因之一）：async 函数的
    // constructor 是 AsyncFunction、Object.prototype.toString 输出
    // "[object AsyncFunction]"，与原生 fetch（普通 Function）不符，
    // 反爬指纹校验直接判改机。普通函数 + then 链保持异步语义。
    const hookedFetch = function(input, init) {
        init = init || {};
        const request = input instanceof Request ? input : null;
        const entry = {
            url: safeUrl(input),
            method: init.method || (request ? request.method : 'GET') || 'GET',
            headerNames: headerNames(init.headers || (request ? request.headers : undefined)),
            body: summarizeBody(init.body),
            timestamp: Date.now()
        };
        push(entry);
        let result;
        try {
            result = _fetch.apply(this, arguments);
        } catch (e) {
            entry.errorName = e && e.name ? String(e.name) : 'Error';
            entry.errorMessage = String(e && e.message || e).slice(0, 150);
            console.log('CODEX_SAFE_FETCH_ERROR:' + JSON.stringify(entry));
            throw e;
        }
        return result.then(function(response) {
            entry.status = response.status;
            entry.ok = response.ok;
            entry.responseUrl = safeUrl(response.url);
            entry.responseHeaderNames = headerNames(response.headers);
            console.log('CODEX_SAFE_FETCH_RESPONSE:' + JSON.stringify(entry));
            try {
                response.clone().text().then(text => {
                    entry.response = summarizeResponseText(text);
                    console.log('CODEX_SAFE_FETCH_BODY:' + JSON.stringify(entry));
                }).catch(() => {});
            } catch (e) {}
            return response;
        }, function(e) {
            entry.errorName = e && e.name ? String(e.name) : 'Error';
            entry.errorMessage = String(e && e.message || e).slice(0, 150);
            console.log('CODEX_SAFE_FETCH_ERROR:' + JSON.stringify(entry));
            throw e;
        });
    };

    if (navigator.sendBeacon) {
        const _sendBeacon = navigator.sendBeacon.bind(navigator);
        const hookedBeacon = function(url, data) {
            const entry = {
                url: safeUrl(url),
                method: 'BEACON',
                body: summarizeBody(data),
                timestamp: Date.now()
            };
            console.log('CODEX_SAFE_BEACON:' + JSON.stringify(entry));
            return _sendBeacon(url, data);
        };
        regFake(hookedBeacon, navigator.sendBeacon);
        replaceOnOwner(navigator, 'sendBeacon', hookedBeacon);
    }

    // 在原生定义处替换（fetch 在 Window.prototype、sendBeacon 在
    // Navigator.prototype），并克隆原描述符（Web IDL  enumerable:true）。
    // 直接给 window/navigator 实例加自有属性会被 hasOwnProperty('fetch')
    // 类指纹校验识别（gsxt 实测卡死根因之二）。
    function replaceOnOwner(startObj, name, fake) {
        try {
            var obj = startObj;
            while (obj) {
                var desc = Object.getOwnPropertyDescriptor(obj, name);
                if (desc) {
                    if ('value' in desc) {
                        desc.value = fake;
                        Object.defineProperty(obj, name, desc);
                    } else {
                        // getter 形态：替换 get，保留 set/enumerable/configurable
                        var origGet = desc.get;
                        desc.get = function() { return fake; };
                        Object.defineProperty(obj, name, desc);
                    }
                    return true;
                }
                obj = Object.getPrototypeOf(obj);
            }
        } catch (e) {}
        try { startObj[name] = fake; } catch (e) {}
        return false;
    }

    // 第十一阶段（gsxt 实测）：登记 toString 守护；包装保持
    // configurable/writable，避免锁死导致目标自身的 fetch 包装抛错卡死。
    regFake(hookedFetch, _fetch);
    replaceOnOwner(window, 'fetch', hookedFetch);
})();
