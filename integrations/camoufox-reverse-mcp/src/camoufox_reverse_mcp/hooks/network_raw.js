(function() {
    if (window.__mcp_raw_network_state) return;
    const documentId = crypto.randomUUID();
    const calls = [];
    const pending = new Set();
    const dispatch = window.dispatchEvent.bind(window);
    const EventClass = CustomEvent;
    const stringify = JSON.stringify.bind(JSON);
    const documentEvent = {type: 'document', document_id: documentId, url: location.href};
    const emit = value => dispatch(new EventClass('__mcp_raw_network', {detail: stringify(value)}));
    function publish(entry) { entry.revision++; emit(entry); }
    function track(promise) {
        pending.add(promise);
        promise.finally(() => pending.delete(promise));
    }
    function bytes(buffer) {
        const data = new Uint8Array(buffer);
        let binary = '';
        for (let i = 0; i < data.length; i += 8192)
            binary += String.fromCharCode(...data.subarray(i, i + 8192));
        return btoa(binary);
    }
    function body(entry, value) {
        if (value == null) {
            entry.body = null; entry.body_base64 = null; entry.body_ready = true;
        } else if (typeof value === 'string' || value instanceof URLSearchParams) {
            entry.body = String(value);
            entry.body_base64 = bytes(new TextEncoder().encode(entry.body));
            entry.body_ready = true;
        } else if (value instanceof ArrayBuffer || ArrayBuffer.isView(value)) {
            const buffer = value instanceof ArrayBuffer ? value : value.buffer.slice(value.byteOffset, value.byteOffset + value.byteLength);
            entry.body_base64 = bytes(buffer); entry.body_ready = true;
        } else if (value instanceof Blob) {
            track(value.arrayBuffer().then(buffer => {
                entry.body_base64 = bytes(buffer); entry.body_ready = true; publish(entry);
            }).catch(error => { entry.body_error = String(error); publish(entry); }));
        } else if (value instanceof FormData) {
            entry.body = [];
            value.forEach((item, name) => {
                const field = {name};
                entry.body.push(field);
                if (typeof item === 'string') field.value = item;
                else {
                    field.filename = item.name; field.mime_type = item.type;
                    track(item.arrayBuffer().then(buffer => { field.base64 = bytes(buffer); publish(entry); })
                        .catch(error => { field.error = String(error); publish(entry); }));
                }
            });
            // The multipart boundary is assigned by the browser. Do not claim
            // byte equality between this input representation and the wire body.
            entry.body_ready = false;
            entry.body_format = 'form-data';
        } else {
            entry.body_format = Object.prototype.toString.call(value);
            entry.body_ready = false;
        }
    }
    function create(type, method, url, stack) {
        const entry = {type, method: String(method).toUpperCase(), url: new URL(String(url), location.href).href,
            document_id: documentId, call_id: documentId + ':' + (calls.length + 1),
            timestamp: Date.now(), stack, headers: [], revision: 0, body_ready: false};
        calls.push(entry);
        return entry;
    }
    window.__mcp_raw_network_state = {
        async drain(timeout = 500) {
            let timer;
            try {
                await Promise.race([
                    (async () => { while (pending.size) await Promise.all(Array.from(pending)); })(),
                    new Promise(resolve => { timer = setTimeout(resolve, timeout); })
                ]);
            } finally { clearTimeout(timer); }
            return {events: [documentEvent, ...calls], pending: pending.size};
        }
    };
    window.addEventListener('__mcp_raw_ready', () => { emit(documentEvent); calls.forEach(emit); });
    emit(documentEvent);
    window.__mcp_install_raw_network = function(type) {
        const flag = '__mcp_' + type + '_hooked';
        if (window[flag]) return;
        window[flag] = true;
        const log = window['__mcp_' + type + '_log'] = window['__mcp_' + type + '_log'] || [];
        if (type === 'fetch') {
            const original = window.fetch;
            window.fetch = function(input, init) {
                const stack = new Error().stack;
                const request = input instanceof Request ? input : null;
                const options = init || {};
                const entry = create(type, options.method || (request && request.method) || 'GET', request ? request.url : input, stack);
                new Headers(options.headers || (request && request.headers) || {}).forEach((value, name) => entry.headers.push([name, value]));
                if (options.body != null || !request || request.body === null) body(entry, options.body);
                else {
                    try {
                        track(request.clone().arrayBuffer().then(buffer => {
                            entry.body_base64 = bytes(buffer); entry.body = new TextDecoder().decode(buffer);
                            entry.body_ready = true; publish(entry);
                        }).catch(error => { entry.body_error = String(error); publish(entry); }));
                    } catch (error) { entry.body_error = String(error); }
                }
                log.push(entry); publish(entry);
                let result;
                try { result = original.apply(this, arguments); }
                catch (error) { entry.error = String(error); publish(entry); throw error; }
                track(result.then(response => {
                    entry.status = response.status; entry.response_url = response.url;
                    entry.response_headers = Array.from(response.headers.entries()); publish(entry);
                    track(response.clone().arrayBuffer().then(buffer => {
                        entry.response_body_base64 = bytes(buffer); publish(entry);
                    }).catch(error => { entry.response_error = String(error); publish(entry); }));
                }, error => { entry.error = String(error); publish(entry); }));
                return result;
            };
        } else {
            const proto = XMLHttpRequest.prototype;
            const originalOpen = proto.open, originalSend = proto.send, originalHeader = proto.setRequestHeader;
            const entries = new WeakMap();
            proto.open = function(method, url) {
                const result = originalOpen.apply(this, arguments);
                entries.set(this, {method, url, headers: []});
                return result;
            };
            proto.setRequestHeader = function(name, value) {
                const result = originalHeader.apply(this, arguments);
                if (entries.has(this)) entries.get(this).headers.push([String(name), String(value)]);
                return result;
            };
            proto.send = function(value) {
                const details = entries.get(this);
                if (!details) return originalSend.apply(this, arguments);
                const entry = create(type, details.method, details.url, new Error().stack);
                entry.headers = details.headers.slice(); body(entry, value); log.push(entry); publish(entry);
                this.addEventListener('loadend', () => {
                    entry.status = this.status; entry.response_url = this.responseURL;
                    entry.response_headers = this.getAllResponseHeaders();
                    try {
                        if (!this.responseType || this.responseType === 'text') {
                            entry.response_body = this.responseText;
                        } else if (this.responseType === 'arraybuffer' && this.response) {
                            entry.response_body_base64 = bytes(this.response);
                        } else if (this.responseType === 'blob' && this.response) {
                            track(this.response.arrayBuffer().then(buffer => { entry.response_body_base64 = bytes(buffer); publish(entry); })
                                .catch(error => { entry.response_error = String(error); publish(entry); }));
                        } else if (this.responseType === 'json') entry.response_body = this.response;
                        else if (this.responseXML) entry.response_body = new XMLSerializer().serializeToString(this.responseXML);
                    } catch (error) { entry.response_error = String(error); }
                    publish(entry);
                }, {once: true});
                try { return originalSend.apply(this, arguments); }
                catch (error) { entry.error = String(error); publish(entry); throw error; }
            };
        }
    };
})();
