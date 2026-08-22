#!/usr/bin/env python3
"""Adversarial tests for the packaged skill self-check (self_check.py)."""

from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SELF_CHECK = ROOT / "skills/diagram-design/scripts/self_check.py"
ASSET_DIR = ROOT / "skills/diagram-design/assets"
TEMPLATE = ASSET_DIR / "template-motion.html"
EXAMPLE = ASSET_DIR / "example-policy-trace-animated.html"
STATIC_EXAMPLE = ASSET_DIR / "example-architecture.html"


def load_self_check():
    spec = importlib.util.spec_from_file_location("self_check", SELF_CHECK)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    module = load_self_check()
    failures: list[str] = []

    def check_pass(label: str, path: Path) -> None:
        errors = module.verify(path)
        if errors:
            failures.append(f"{label}: expected pass, got {errors}")
        else:
            print(f"OK: {label} passes")

    def check_fail(label: str, source: str, needle: str) -> None:
        with tempfile.TemporaryDirectory() as scratch:
            candidate = Path(scratch) / "candidate.html"
            candidate.write_text(source, encoding="utf-8")
            errors = module.verify(candidate)
        if not any(needle in error for error in errors):
            failures.append(f"{label}: expected an error containing {needle!r}, got {errors}")
        else:
            print(f"OK: {label} rejected")

    def check_redacted(label: str, source: str, secret: str) -> None:
        with tempfile.TemporaryDirectory() as scratch:
            candidate = Path(scratch) / "candidate.html"
            candidate.write_text(source, encoding="utf-8")
            errors = module.verify(candidate)
        if not errors:
            failures.append(f"{label}: expected rejection")
        elif secret in "\n".join(errors):
            failures.append(f"{label}: secret appeared in diagnostics: {errors}")
        else:
            print(f"OK: {label} diagnostics redact the secret")

    check_pass("shipped template", TEMPLATE)
    check_pass("shipped animated example", EXAMPLE)
    check_pass("shipped static example", STATIC_EXAMPLE)

    static = STATIC_EXAMPLE.read_text(encoding="utf-8")
    animated = EXAMPLE.read_text(encoding="utf-8")

    check_fail(
        "executable attribute",
        static.replace("<body>", '<body onload="fetch(1)">', 1),
        "executable attribute",
    )
    check_fail(
        "remote image",
        static.replace("<body>", '<body><img src="https://tracker.example/p.gif">', 1),
        "external reference",
    )
    check_fail(
        "relative image",
        static.replace("<body>", '<body><img src="tracker.gif">', 1),
        "external reference",
    )
    check_fail(
        "same-origin absolute image",
        static.replace("<body>", '<body><img src="/tracker.gif">', 1),
        "external reference",
    )
    check_fail(
        "credential-bearing Google Fonts URL",
        static.replace(
            "https://fonts.googleapis.com/css2",
            "https://user:secret@fonts.googleapis.com/css2",
            1,
        ),
        "credential-free Google Fonts",
    )
    check_fail(
        "Google Fonts URL with unexpected query key",
        static.replace("&display=swap", "&display=swap&token=secret", 1),
        "credential-free Google Fonts",
    )
    check_fail(
        "Google Fonts URL with blank unexpected query key",
        static.replace("&display=swap", "&tracking-secret=&display=swap", 1),
        "credential-free Google Fonts",
    )
    check_fail(
        "Google Fonts URL with bare unexpected query key",
        static.replace("&display=swap", "&tracking-secret&display=swap", 1),
        "credential-free Google Fonts",
    )
    check_fail(
        "Google Fonts URL with malformed port",
        static.replace("fonts.googleapis.com", "fonts.googleapis.com:bad", 1),
        "credential-free Google Fonts",
    )
    check_redacted(
        "credential URL",
        static.replace(
            "https://fonts.googleapis.com/css2",
            "https://user:sentinel-secret@fonts.googleapis.com/css2",
            1,
        ),
        "sentinel-secret",
    )
    check_fail(
        "SVG xml base",
        static.replace("<svg ", '<svg xml:base="https://tracker.example/" ', 1),
        "xml:base attribute",
    )
    check_fail(
        "SVG declarative href mutation",
        static.replace(
            "</svg>",
            '<image><set attributeName="href" to="https://tracker.example/p.gif" '
            'begin="0s"/></image></svg>',
            1,
        ),
        "SVG declarative animation",
    )
    check_fail(
        "mixed data and remote srcset",
        static.replace(
            "<body>",
            '<body><img srcset="data:image/gif;base64,R0lGODlhAQABAIAAAAUEBA== 1x, '
            'https://tracker.example/p.gif 2x">',
            1,
        ),
        "srcset attribute",
    )
    check_fail(
        "preload imagesrcset",
        static.replace(
            "</head>",
            '<link rel="preload" as="image" imagesrcset="https://tracker.example/p.png 1x">'
            "</head>",
            1,
        ),
        "imagesrcset attribute",
    )
    check_fail(
        "meta refresh",
        static.replace(
            "</head>",
            '<meta http-equiv="refresh" content="0;url=https://tracker.example/">'
            "</head>",
            1,
        ),
        "http-equiv <meta>",
    )
    check_fail(
        "link ping",
        static.replace(
            "<body>",
            '<body><a href="#local" ping="https://tracker.example/ping">local</a>',
            1,
        ),
        "ping attribute",
    )
    check_fail(
        "image attribution source",
        static.replace(
            "<body>",
            '<body><img src="data:image/gif;base64,R0lGODlhAQABAIAAAAUEBA==" '
            'attributionsrc="https://tracker.example/register">',
            1,
        ),
        "attributionsrc attribute",
    )
    check_fail(
        "link attribution source",
        static.replace(
            "<body>",
            '<body><a href="#local" attributionsrc="https://tracker.example/register">x</a>',
            1,
        ),
        "attributionsrc attribute",
    )
    check_fail(
        "remote CSS URL",
        static.replace(
            "</style>",
            ".beacon { background-image: url(https://tracker.example/p.gif); }</style>",
            1,
        ),
        "non-fragment CSS url()",
    )
    check_fail(
        "remote inline CSS URL",
        static.replace(
            "<body>",
            '<body style="background-image:url(https://tracker.example/p.gif)">',
            1,
        ),
        "non-fragment CSS url()",
    )
    check_fail(
        "CSS import",
        static.replace(
            "</style>",
            '@import "https://tracker.example/style.css";</style>',
            1,
        ),
        "CSS @import",
    )
    check_fail(
        "CSS image-set string source",
        static.replace(
            "</style>",
            '.beacon { background-image: image-set("https://tracker.example/p.gif" 1x); }</style>',
            1,
        ),
        "CSS image-set()",
    )
    check_fail(
        "escaped CSS image-set string source",
        static.replace(
            "</style>",
            r'.beacon { background-image: image-s\65t("https://tracker.example/p.gif" 1x); }</style>',
            1,
        ),
        "CSS image-set()",
    )
    check_fail(
        "CSS src string source",
        static.replace(
            "</style>",
            '.beacon { background: src("https://tracker.example/p.gif"); }</style>',
            1,
        ),
        "CSS src()",
    )
    check_fail(
        "escaped CSS src string source",
        static.replace(
            "</style>",
            r'.beacon { background: s\000072c("https://tracker.example/p.gif"); }</style>',
            1,
        ),
        "CSS src()",
    )
    check_fail(
        "inline CSS image string source",
        static.replace(
            "<body>",
            '<body style="background:image(https://tracker.example/p.gif)">',
            1,
        ),
        "CSS image()",
    )
    check_fail(
        "SVG presentation CSS image string source",
        static.replace(
            "<svg ",
            '<svg fill="image(https://tracker.example/p.gif)" ',
            1,
        ),
        "CSS image()",
    )
    check_fail(
        "SVG style script foreign-content differential",
        static.replace(
            "</svg>",
            "<style><script>document.documentElement.dataset.pwn='yes'</script></style></svg>",
            1,
        ),
        "direct child of HTML <head>",
    )
    check_fail(
        "SVG style image foreign-content differential",
        static.replace(
            "</svg>",
            '<style><image href="https://tracker.example/p.gif"></style></svg>',
            1,
        ),
        "direct child of HTML <head>",
    )
    check_fail(
        "escaped CSS URL",
        static.replace(
            "</style>",
            r".beacon { background-image: u\72l(https://tracker.example/p.gif); }</style>",
            1,
        ),
        "non-fragment CSS url()",
    )
    check_fail(
        "comment-obfuscated CSS import",
        static.replace(
            "</style>",
            '@/**/import "https://tracker.example/style.css";</style>',
            1,
        ),
        "CSS @import",
    )
    check_fail(
        "multiline-comment-obfuscated CSS import",
        static.replace(
            "</style>",
            '@im/*\n*/port "https://tracker.example/x.css";</style>',
            1,
        ),
        "CSS @import",
    )
    check_fail(
        "multiline-comment-obfuscated CSS URL",
        static.replace(
            "</style>",
            ".x { background: u/*\n*/rl(https://tracker.example/x.png); }</style>",
            1,
        ),
        "CSS url",
    )
    check_fail(
        "quoted comment opener cannot hide remote CSS URL",
        static.replace(
            "</style>",
            'body { --proof: "/*"; background-image: '
            "url(https://tracker.example/p.gif); } /* */</style>",
            1,
        ),
        "non-fragment CSS url()",
    )
    check_fail(
        "remote SVG presentation URL",
        static.replace(
            "<svg ",
            '<svg fill="url(https://tracker.example/p.svg#paint)" ',
            1,
        ),
        "non-fragment CSS url()",
    )
    check_fail(
        "escaped remote SVG presentation URL",
        static.replace(
            "<svg ",
            r'<svg fill="u\72l(https://tracker.example/p.svg#paint)" ',
            1,
        ),
        "non-fragment CSS url()",
    )
    check_fail(
        "entity CRLF inside escaped CSS URL",
        static.replace(
            "<body>",
            r'<body style="background:u\000072&#13;&#10;l(https://tracker.example/p.gif)">',
            1,
        ),
        "non-fragment CSS url()",
    )
    check_fail(
        "invalid CSS escape does not hide later URL",
        static.replace(
            "</style>",
            r".beacon { x: \FFFFFF; background: url(https://tracker.example/p.gif); }</style>",
            1,
        ),
        "non-fragment CSS url()",
    )
    spoofed_motion_fallbacks = animated.replace(
        "prefers-reduced-motion: reduce", "prefers-no-motion: reduce"
    ).replace("@media print", "@media screen")
    spoofed_motion_fallbacks = spoofed_motion_fallbacks.replace(
        "<body>",
        '<body style="--reduced:prefers-reduced-motion:reduce;--print:@media print">',
        1,
    )
    check_fail(
        "inline CSS cannot spoof motion fallbacks",
        spoofed_motion_fallbacks,
        "missing reduced-motion CSS fallback",
    )
    stylesheet_spoof = animated.replace(
        "prefers-reduced-motion: reduce", "prefers-no-motion: reduce"
    ).replace("@media print", "@media screen")
    stylesheet_spoof = stylesheet_spoof.replace(
        "</style>",
        ":root { --proof: prefers-reduced-motion: reduce; --print: @media print; }"
        "</style>",
        1,
    )
    check_fail(
        "stylesheet text cannot spoof motion fallbacks",
        stylesheet_spoof,
        "missing reduced-motion CSS fallback",
    )
    quoted_at_rule_spoof = animated.replace(
        "prefers-reduced-motion: reduce", "prefers-no-motion: reduce"
    ).replace("@media print", "@media screen")
    quoted_at_rule_spoof = quoted_at_rule_spoof.replace(
        "</style>",
        ':root { --proof: "@media (prefers-reduced-motion: reduce) {}"; '
        '--print: "@media print {}"; }</style>',
        1,
    )
    check_fail(
        "quoted CSS cannot spoof motion fallbacks",
        quoted_at_rule_spoof,
        "missing reduced-motion CSS fallback",
    )
    invalid_prelude_spoof = animated.replace(
        "prefers-reduced-motion: reduce", "prefers-no-motion: reduce"
    ).replace("@media print", "@media screen")
    invalid_prelude_spoof = invalid_prelude_spoof.replace(
        "</style>",
        "bogus @media (prefers-reduced-motion: reduce) {} "
        "bogus @media print {}</style>",
        1,
    )
    check_fail(
        "invalid CSS prelude cannot spoof motion fallbacks",
        invalid_prelude_spoof,
        "missing reduced-motion CSS fallback",
    )
    nested_prelude_spoof = animated.replace(
        "prefers-reduced-motion: reduce", "prefers-no-motion: reduce"
    ).replace("@media print", "@media screen")
    nested_prelude_spoof = nested_prelude_spoof.replace(
        "</style>",
        "foo(; @media (prefers-reduced-motion: reduce) {}) {} "
        "foo(; @media print {}) {}</style>",
        1,
    )
    check_fail(
        "nested invalid CSS prelude cannot spoof motion fallbacks",
        nested_prelude_spoof,
        "missing reduced-motion CSS fallback",
    )
    comment_joined_at_rule_spoof = animated.replace(
        "prefers-reduced-motion: reduce", "prefers-no-motion: reduce"
    ).replace("@media print", "@media screen")
    comment_joined_at_rule_spoof = comment_joined_at_rule_spoof.replace(
        "</style>",
        "@me/**/dia (prefers-reduced-motion: reduce) {} "
        "@me/**/dia print {}</style>",
        1,
    )
    check_fail(
        "comment-joined CSS at-rule cannot spoof motion fallbacks",
        comment_joined_at_rule_spoof,
        "missing reduced-motion CSS fallback",
    )
    split_stylesheet_spoof = animated.replace(
        "prefers-reduced-motion: reduce", "prefers-no-motion: reduce"
    ).replace("@media print", "@media screen")
    split_stylesheet_spoof = split_stylesheet_spoof.replace(
        "</head>",
        "<style>@me</style><style>dia (prefers-reduced-motion: reduce) {}</style>"
        "<style>@me</style><style>dia print {}</style></head>",
        1,
    )
    check_fail(
        "split stylesheets cannot spoof motion fallbacks",
        split_stylesheet_spoof,
        "missing reduced-motion CSS fallback",
    )
    disabled_stylesheet_spoof = animated.replace(
        "prefers-reduced-motion: reduce", "prefers-no-motion: reduce"
    ).replace("@media print", "@media screen")
    disabled_stylesheet_spoof = disabled_stylesheet_spoof.replace(
        "</head>",
        '<style media="not all">@media (prefers-reduced-motion: reduce) {} '
        "@media print {}</style></head>",
        1,
    )
    check_fail(
        "disabled stylesheet cannot spoof motion fallbacks",
        disabled_stylesheet_spoof,
        "missing reduced-motion CSS fallback",
    )
    self_closing_template_spoof = animated.replace(
        "<style>", "<template/><style>", 1
    )
    check_fail(
        "self-closing template syntax",
        self_closing_template_spoof,
        "self-closing syntax",
    )
    duplicate_media_spoof = animated.replace(
        "<style>", '<style media="not all" media="all">', 1
    )
    check_fail(
        "duplicate media attributes",
        duplicate_media_spoof,
        "duplicate attribute media",
    )
    duplicate_role_spoof = static.replace(
        'role="img"', 'role="presentation" role="img"', 1
    )
    check_fail(
        "duplicate role attributes",
        duplicate_role_spoof,
        "duplicate attribute role",
    )
    for label, wrapper in (
        (
            "non-CSS style type",
            '<style type="text/plain">@media (prefers-reduced-motion: reduce) {} '
            "@media print {}</style>",
        ),
        (
            "template-contained style",
            "<template><style>@media (prefers-reduced-motion: reduce) {} "
            "@media print {}</style></template>",
        ),
        (
            "noscript-contained style",
            "<noscript><style>@media (prefers-reduced-motion: reduce) {} "
            "@media print {}</style></noscript>",
        ),
        (
            "noembed-contained style",
            "<noembed><style>@media (prefers-reduced-motion: reduce) {} "
            "@media print {}</style></noembed>",
        ),
        (
            "noframes-contained style",
            "<noframes><style>@media (prefers-reduced-motion: reduce) {} "
            "@media print {}</style></noframes>",
        ),
        (
            "textarea-contained style",
            "<textarea><style>@media (prefers-reduced-motion: reduce) {} "
            "@media print {}</style></textarea>",
        ),
    ):
        inert_stylesheet_spoof = animated.replace(
            "prefers-reduced-motion: reduce", "prefers-no-motion: reduce"
        ).replace("@media print", "@media screen")
        inert_stylesheet_spoof = inert_stylesheet_spoof.replace(
            "</head>", wrapper + "</head>", 1
        )
        check_fail(
            f"{label} cannot spoof motion fallbacks",
            inert_stylesheet_spoof,
            (
                "<noscript> must contain exactly one plain-text paragraph"
                if label == "noscript-contained style"
                else "missing reduced-motion CSS fallback"
            ),
        )
    for label, open_tag, close_tag in (
        ("template-contained controller", "<template>", "</template>"),
        ("noembed-contained controller", "<noembed>", "</noembed>"),
        ("noframes-contained controller", "<noframes>", "</noframes>"),
        ("textarea-contained controller", "<textarea>", "</textarea>"),
    ):
        inert_controller = animated.replace(
            '<script data-diagram-controls>',
            open_tag + '<script data-diagram-controls>',
            1,
        ).replace("</script>", "</script>" + close_tag, 1)
        check_fail(label, inert_controller, "parser-mode element")
    for label, open_tag, close_tag in (
        ("template-contained SVG", "<template>", "</template>"),
        ("noembed-contained SVG", "<noembed>", "</noembed>"),
        ("noframes-contained SVG", "<noframes>", "</noframes>"),
        ("textarea-contained SVG", "<textarea>", "</textarea>"),
        ("xmp-contained SVG", "<xmp>", "</xmp>"),
        ("plaintext-contained SVG", "<plaintext>", ""),
    ):
        inert_svg = static.replace("<svg ", open_tag + "<svg ", 1).replace(
            "</svg>", "</svg>" + close_tag, 1
        )
        check_fail(label, inert_svg, "parser-mode element")
    for label, wrapper in (
        ("select-contained diagram", "select"),
        ("frameset-contained diagram", "frameset"),
    ):
        check_fail(
            label,
            static.replace("<body>", f"<body><{wrapper}>", 1).replace(
                "</body>", f"</{wrapper}></body>", 1
            ),
            "parser-mode element",
        )
    check_fail(
        "lookalike fonts host",
        static.replace(
            "https://fonts.googleapis.com/css2",
            "https://fonts.googleapis.com.evil.tld/css2",
            1,
        ),
        "credential-free Google Fonts",
    )
    check_fail(
        "javascript URL",
        static.replace("<body>", '<body><a href="javascript:alert(1)">x</a>', 1),
        "executable URL",
    )
    check_fail(
        "data html URL",
        static.replace("<body>", '<body><a href="data:text/html,x">x</a>', 1),
        "executable URL",
    )
    check_fail(
        "iframe injection",
        static.replace("<body>", "<body><iframe></iframe>", 1),
        "not allowed",
    )
    check_fail(
        "abrupt comment browser differential",
        static.replace(
            "</body>", '<!--><img src="https://tracker.example/p.gif">--></body>', 1
        ),
        "abrupt HTML comment syntax",
    )
    check_fail(
        "marked section browser differential",
        static.replace(
            "</body>",
            '<![CDATA[><img src="bad:" onerror="alert(1)">]]></body>',
            1,
        ),
        "marked-section syntax",
    )
    check_fail(
        "malformed marked section preflight",
        static + "<![]>",
        "marked-section syntax",
    )
    check_fail(
        "title canonical-closer browser differential",
        static.replace(
            "<title>",
            '<title><p title="</title><script>'
            "document.documentElement.dataset.pwned=1</script>\"></p>",
            1,
        ),
        "<title> must contain plain text",
    )
    check_fail(
        "noscript canonical-closer browser differential",
        static.replace(
            "<svg ",
            '<noscript><p title="</noscript><img src=https://tracker.example/p.gif '
            'onerror=alert(1)>"></p><svg ',
            1,
        ),
        "<noscript> must contain exactly one plain-text paragraph",
    )
    check_fail(
        "raw style closing tag browser differential",
        static.replace(
            "</style>",
            '</style media=all><img src="https://tracker.example/p.gif"></style>',
            1,
        ),
        "noncanonical raw-text closing tag",
    )
    for label, separator in (
        ("NBSP raw style closing differential", "\N{NO-BREAK SPACE}"),
        ("vertical-tab raw style closing differential", "\v"),
    ):
        check_fail(
            label,
            static.replace(
                "<style>",
                f"<style>/*</style{separator}>*/"
                "@import url(https://tracker.example/p.css);/*<style>*/",
                1,
            ),
            "noncanonical raw-text closing tag",
        )
    for label, separator in (
        ("ASCII-space before raw style name", " "),
        ("NBSP before raw style name", "\N{NO-BREAK SPACE}"),
    ):
        check_fail(
            label,
            static.replace(
                "<style>",
                f"<style>/*</{separator}style>*/"
                "@import url(https://tracker.example/p.css);/*<style>*/",
                1,
            ),
            "whitespace before a raw-text closing tag name",
        )
    for label, closing in (
        ("ASCII-space before noscript name", "</ noscript>"),
        ("NBSP before noscript name", "</\N{NO-BREAK SPACE}noscript>"),
        ("NBSP after noscript name", "</noscript\N{NO-BREAK SPACE}>"),
        ("vertical-tab after noscript name", "</noscript\v>"),
    ):
        check_fail(
            label,
            static.replace("<svg ", f"<noscript>proof{closing}<svg ", 1),
            "raw-text closing tag",
        )
    check_fail(
        "arbitrary script",
        static.replace("</body>", "<script>alert(1)</script></body>", 1),
        "canonical data-diagram-controls attribute",
    )
    check_fail(
        "missing svg role",
        static.replace('role="img"', 'role="presentation"', 1),
        "role=img",
    )
    check_fail(
        "modified controller",
        animated.replace("'motion-ready'", "'motion-ready' /* patched */", 1),
        "exactly match the controller",
    )
    check_fail(
        "second script",
        animated.replace("</body>", "<script data-x></script></body>", 1),
        "at most one script",
    )
    check_fail(
        "hidden motion item",
        animated.replace(
            '<g data-motion-item data-step="1"',
            '<g style="opacity:0" data-motion-item data-step="1"',
            1,
        ),
        "hidden in source",
    )
    check_fail(
        "missing noscript",
        animated.replace("<noscript>", "<div>", 1).replace("</noscript>", "</div>", 1),
        "<noscript>",
    )
    check_fail(
        "non-decimal step count",
        animated.replace('data-step-count="5"', 'data-step-count="٥"', 1),
        "ASCII decimal",
    )
    check_fail(
        "nested form motion root",
        animated.replace(
            "<main data-motion-root", "<form><form data-motion-root", 1
        ).replace("</main>", "</form></form>", 1),
        "direct-body <main>",
    )
    check_fail(
        "table motion root",
        animated.replace("<main data-motion-root", "<table data-motion-root", 1).replace(
            "</main>", "</table>", 1
        ),
        "direct-body <main>",
    )

    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        return 1
    print("All self-check tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
