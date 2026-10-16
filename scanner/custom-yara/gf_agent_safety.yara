//////////////////////////////////////////////////////////////////////////////
// GF Agent-Safety YARA rules
//
// Foundation-specific detections layered on top of cisco-ai-skill-scanner's
// built-in rules. Derived from the skill-vetter RED FLAGS
// (clawhub.ai/spclaudehome/skill-vetter).
//
// Loaded in CI via:  skill-scanner scan-all . --custom-rules scanner/custom-yara
//
// Covers three checks not handled by the scanner's built-ins: agent-memory/
// identity theft, raw-IP exfiltration, and browser-data theft.
//
// DESIGN NOTE — exclusions:
//   These rules deliberately do NOT use whole-file `not $x` exclusions. In YARA
//   `not $x` means "x appears NOWHERE in the file", so a single ``` fence, a
//   `README` mention, or one `127.0.0.1` anywhere would disable the entire rule
//   for the whole file — trivially bypassable and the source of silent false
//   negatives. As a security gate these rules favor RECALL; benign matches are
//   triaged by the human reviewer. The behavior contract is pinned by
//   scanner/tests/test_yara_rules.py.
//////////////////////////////////////////////////////////////////////////////

rule vetter_agent_memory_theft {

    meta:
        author = "GF AI Skills (derived from skill-vetter)"
        description = "Detects skills that read agent memory, identity, or personality files to steal context or impersonate the agent"
        classification = "harmful"
        threat_type = "AGENT MEMORY THEFT"

    strings:
        // Agent memory / identity files
        $memory_md    = "MEMORY.md" nocase
        $user_md      = "USER.md" nocase
        $soul_md      = "SOUL.md" nocase
        $identity_md  = "IDENTITY.md" nocase

        // Claude Code-specific config / memory paths (suspicious on their own)
        $claude_memory   = ".claude/memory" nocase
        $claude_settings = ".claude/settings" nocase
        $claude_config   = "claude_desktop_config.json" nocase

        // File-access actions
        $open_call  = /\b(open|read|cat|head|tail)\s*\(/
        $path_read  = /Path\s*\([^)]+\)\.(read_text|read_bytes)/

    condition:
        (
            ($memory_md or $user_md or $soul_md or $identity_md)
            and ($open_call or $path_read)
        )
        or any of ($claude_memory, $claude_settings, $claude_config)
}

rule vetter_ip_exfiltration {

    meta:
        author = "GF AI Skills (derived from skill-vetter)"
        description = "Detects network calls to raw IP addresses instead of domain names, which may bypass DNS logging and content filtering"
        classification = "harmful"
        threat_type = "IP-BASED EXFILTRATION"

    strings:
        // A network call to a raw IPv4 address (the http(s):// / curl / connect
        // context is required, so bare IPs in prose do not match). Private and
        // loopback ranges are intentionally NOT whole-file-excluded (that was
        // bypassable); a raw-IP callback in a skill is worth a reviewer's look.
        $http_ip   = /https?:\/\/\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}/
        $socket_ip = /connect\s*\(\s*\(?\s*['"]\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}/
        $curl_ip   = /\b(curl|wget)\s+[^\n]*https?:\/\/\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}/

    condition:
        any of them
}

rule vetter_browser_data_theft {

    meta:
        author = "GF AI Skills (derived from skill-vetter)"
        description = "Detects skills that access browser cookies, sessions, saved passwords, or profile data"
        classification = "harmful"
        threat_type = "BROWSER DATA THEFT"

    strings:
        // Browser profile paths
        $chrome_path   = /Google\/Chrome\/(Default|Profile)/ nocase
        $firefox_path  = /\.mozilla\/firefox\/[^\s]*profiles/ nocase
        $brave_path    = "BraveSoftware" nocase
        $chromium_path = /Chromium\/(Default|Profile)/ nocase
        $edge_path     = "Microsoft/Edge" nocase
        $mac_chrome    = "Library/Application Support/Google/Chrome" nocase

        // Cookie / session / credential store files
        $cookies_db      = "Cookies" nocase
        $login_data      = "Login Data" nocase
        $web_data        = "Web Data" nocase
        $local_storage   = "Local Storage" nocase
        $session_storage = "Session Storage" nocase

        // sqlite opening a browser store
        $sqlite_cookies = /sqlite3[^\n]*(Cookies|Login Data|Web Data)/i

    condition:
        // A concrete browser-profile path, or sqlite on a browser store, or a
        // credential-store filename co-occurring with a browser path.
        any of ($chrome_path, $firefox_path, $brave_path, $chromium_path, $edge_path, $mac_chrome)
        or $sqlite_cookies
        or (
            any of ($cookies_db, $login_data, $web_data, $local_storage, $session_storage)
            and any of ($chrome_path, $firefox_path, $mac_chrome, $chromium_path)
        )
}
