"""csp_blocks_inline_rewrite 护栏单测（第十三阶段 hCaptcha 复现裁决修复）。

实测形态：hcaptcha widget iframe 文档 meta CSP
`script-src 'self' 'unsafe-eval' 'sha256-...' ——内联脚本按哈希白名单放行，
route 层改写内联脚本后哈希不匹配，整段引导脚本被 CSP 阻止，widget 不初始化。
"""
from camoufox_reverse_mcp.tools.vm_loop import csp_blocks_inline_rewrite

HCAPTCHA_META = (
    '<!DOCTYPE html><html><head>'
    '<meta http-equiv="Content-Security-Policy" content="object-src \'none\'; '
    'base-uri \'self\'; worker-src blob:; script-src \'self\' \'unsafe-eval\' '
    '\'sha256-7Hv+kKGcrwd8bnOvVwta5VsMiGHyqI1usqoT1LhiL9w=\';">'
    '</head><body></body></html>')


def test_meta_csp_hash_blocks():
    assert csp_blocks_inline_rewrite({}, HCAPTCHA_META) is True


def test_header_csp_hash_blocks():
    headers = {"content-security-policy":
               "default-src 'none'; script-src 'self' 'sha384-abc123=='"}
    assert csp_blocks_inline_rewrite(headers, "<html></html>") is True


def test_default_src_hash_fallback_blocks():
    headers = {"Content-Security-Policy": "default-src 'self' 'sha512-zzz'"}
    assert csp_blocks_inline_rewrite(headers, "<html></html>") is True


def test_unsafe_inline_does_not_save_hash():
    # CSP3：hash/nonce 源在位时 'unsafe-inline' 被忽略，仍必须禁止改写
    headers = {"content-security-policy":
               "script-src 'unsafe-inline' 'sha256-abc=='"}
    assert csp_blocks_inline_rewrite(headers, "<html></html>") is True


def test_no_hash_allows_rewrite():
    headers = {"content-security-policy": "script-src 'self' 'unsafe-inline'"}
    assert csp_blocks_inline_rewrite(headers, "<html></html>") is False


def test_no_csp_allows_rewrite():
    assert csp_blocks_inline_rewrite({}, "<html><script>1</script></html>") is False


def test_unrelated_meta_ignored():
    html = '<meta http-equiv="refresh" content="5"><script>while(1){}</script>'
    assert csp_blocks_inline_rewrite({}, html) is False
