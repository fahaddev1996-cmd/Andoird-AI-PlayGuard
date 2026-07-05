"""
PlayGuard AI — MCP Server
Exposes APK analysis tools via Model Context Protocol.
The agent (backend/agent.py) calls these tools autonomously in a ReAct loop.
"""

import sys, os, re, json, zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from mcp.server.fastmcp import FastMCP

try:
    from androguard.core.apk import APK as AndroAPK
    HAS_ANDROGUARD = True
except ImportError:
    HAS_ANDROGUARD = False

mcp = FastMCP("playguard-agent-tools")

# ── Permission data ───────────────────────────────────────────
CRITICAL_PERMS = [
    "android.permission.READ_SMS", "android.permission.RECEIVE_SMS",
    "android.permission.SEND_SMS", "android.permission.READ_CALL_LOG",
    "android.permission.WRITE_CALL_LOG", "android.permission.MANAGE_EXTERNAL_STORAGE",
    "android.permission.INSTALL_PACKAGES", "android.permission.REQUEST_INSTALL_PACKAGES",
    "android.permission.BIND_ACCESSIBILITY_SERVICE", "android.permission.QUERY_ALL_PACKAGES",
    "android.permission.ACCESS_BACKGROUND_LOCATION",
]
HIGH_PERMS = [
    "android.permission.ACCESS_FINE_LOCATION", "android.permission.CAMERA",
    "android.permission.RECORD_AUDIO", "android.permission.READ_CONTACTS",
    "android.permission.READ_PHONE_STATE", "android.permission.SYSTEM_ALERT_WINDOW",
    "android.permission.WRITE_SETTINGS", "android.permission.READ_EXTERNAL_STORAGE",
    "android.permission.WRITE_EXTERNAL_STORAGE",
]
REQUIRES_DECLARATION = [
    "android.permission.READ_SMS", "android.permission.RECEIVE_SMS",
    "android.permission.SEND_SMS", "android.permission.READ_CALL_LOG",
    "android.permission.MANAGE_EXTERNAL_STORAGE",
    "android.permission.BIND_ACCESSIBILITY_SERVICE",
    "android.permission.ACCESS_BACKGROUND_LOCATION",
]


# ════════════════════════════════════════════════════════════════
# MCP TOOLS — Agent calls these autonomously
# ════════════════════════════════════════════════════════════════

@mcp.tool()
def scan_apk(filepath: str) -> dict:
    """
    TOOL 1: Scan APK/AAB file and extract all metadata + run policy checks.
    Returns package info, SDK levels, permissions, security flags, signing status,
    and a list of policy findings with severity (CRITICAL/HIGH/MEDIUM/PASS).
    Agent calls this first to understand the app's current compliance state.
    """
    if not os.path.exists(filepath):
        return {"error": f"File not found: {filepath}"}

    meta = _parse_apk(filepath)
    findings = _run_checks(meta)

    weights = {"CRITICAL": -30, "HIGH": -15, "MEDIUM": -7, "LOW": -2, "INFO": 0, "PASS": 5}
    score = max(0, min(100, 100 + sum(weights.get(f["severity"], 0) for f in findings)))

    return {
        "score": score,
        "package_name": meta.get("package_name"),
        "app_name": meta.get("app_name"),
        "version_name": meta.get("version_name"),
        "target_sdk": meta.get("target_sdk"),
        "min_sdk": meta.get("min_sdk"),
        "is_aab": meta.get("is_aab"),
        "is_signed": meta.get("is_signed"),
        "permissions": meta.get("permissions", []),
        "findings": findings,
        "critical_count": sum(1 for f in findings if f["severity"] == "CRITICAL"),
        "high_count": sum(1 for f in findings if f["severity"] == "HIGH"),
        "passed_count": sum(1 for f in findings if f["severity"] == "PASS"),
    }


@mcp.tool()
def check_permissions(permissions: list) -> dict:
    """
    TOOL 2: Deep-analyze a list of Android permissions.
    Returns risk classification, which permissions need Google's Declaration Form,
    and what data types must be declared in the Play Console Data Safety form.
    Agent calls this after scan_apk to understand permission-specific risks.
    """
    critical = [p for p in permissions if p in CRITICAL_PERMS]
    high = [p for p in permissions if p in HIGH_PERMS]
    needs_form = [p for p in permissions if p in REQUIRES_DECLARATION]

    data_safety = []
    if any("LOCATION" in p for p in permissions): data_safety.append("Precise location")
    if "android.permission.CAMERA" in permissions: data_safety.append("Photos and videos")
    if "android.permission.RECORD_AUDIO" in permissions: data_safety.append("Audio files / Microphone")
    if any("CONTACTS" in p for p in permissions): data_safety.append("Contacts")
    if any("READ_SMS" in p or "RECEIVE_SMS" in p for p in permissions): data_safety.append("SMS or MMS")
    if any("CALENDAR" in p for p in permissions): data_safety.append("Calendar events")

    return {
        "total_permissions": len(permissions),
        "critical_permissions": critical,
        "high_permissions": high,
        "needs_declaration_form": needs_form,
        "declaration_form_required": len(needs_form) > 0,
        "data_safety_types_to_declare": data_safety,
        "risk_summary": (
            "CRITICAL — immediate rejection risk" if critical else
            "HIGH — manual review likely" if high else
            "MEDIUM — low rejection risk"
        ),
    }


@mcp.tool()
def generate_store_listing(app_name: str, package_name: str, features: str) -> dict:
    """
    TOOL 3: Generate a Play Store-compliant store listing draft.
    Creates a short description, long description, and keyword list
    following Google's metadata policy (no promotional superlatives,
    no misleading claims, proper length limits).
    Agent calls this when store listing content is missing or needs improvement.
    Args:
        app_name: Name of the app
        package_name: App package identifier
        features: Comma-separated list of app features/purpose
    """
    feature_list = [f.strip() for f in features.split(",") if f.strip()]

    short_desc = f"{app_name} helps you {feature_list[0].lower() if feature_list else 'manage your tasks'} quickly and reliably."
    if len(short_desc) > 80:
        short_desc = short_desc[:77] + "..."

    long_desc_parts = [
        f"{app_name} is designed to make {feature_list[0].lower() if feature_list else 'your work'} easier.",
        "",
        "Key features:",
    ]
    for feat in feature_list[:6]:
        long_desc_parts.append(f"• {feat}")
    long_desc_parts += [
        "",
        "Simple, fast, and reliable — built with your privacy in mind.",
    ]
    long_desc = "\n".join(long_desc_parts)

    policy_warnings = []
    banned_words = ["best", "free", "#1", "top", "amazing", "guaranteed", "perfect", "fastest"]
    for word in banned_words:
        if word.lower() in (app_name + " " + features).lower():
            policy_warnings.append(f"Remove promotional word: '{word}'")

    return {
        "short_description": short_desc,
        "short_description_length": len(short_desc),
        "long_description": long_desc,
        "long_description_length": len(long_desc),
        "policy_warnings": policy_warnings,
        "policy_compliant": len(policy_warnings) == 0,
        "note": "Review before publishing — agent-generated draft. Customize for your app.",
    }


@mcp.tool()
def build_data_safety_checklist(permissions: list, has_ads: bool, has_payments: bool, has_account: bool) -> dict:
    """
    TOOL 4: Build the Play Console Data Safety form checklist.
    Returns a structured checklist of every data type that must be declared,
    whether data is shared with third parties, and deletion requirements.
    Agent calls this to produce the compliance checklist the developer must complete.
    Args:
        permissions: List of declared Android permissions
        has_ads: Whether the app shows advertisements
        has_payments: Whether the app has in-app purchases or payments
        has_account: Whether the app has user accounts/login
    """
    declarations = []

    if any("LOCATION" in p for p in permissions):
        declarations.append({"type": "Location", "collection": "Yes", "shared": "No", "required": True})
    if "android.permission.CAMERA" in permissions:
        declarations.append({"type": "Photos and videos", "collection": "Yes", "shared": "No", "required": True})
    if "android.permission.RECORD_AUDIO" in permissions:
        declarations.append({"type": "Audio files", "collection": "Yes", "shared": "No", "required": True})
    if any("CONTACTS" in p for p in permissions):
        declarations.append({"type": "Contacts", "collection": "Yes", "shared": "No", "required": True})
    if has_ads:
        declarations.append({"type": "Advertising ID", "collection": "Yes", "shared": "Yes (Ad networks)", "required": True})
    if has_payments:
        declarations.append({"type": "Financial info (purchase history)", "collection": "Yes", "shared": "No", "required": True})
    if has_account:
        declarations.append({"type": "Personal info (name, email)", "collection": "Yes", "shared": "No", "required": True})
        declarations.append({"type": "Account deletion", "collection": "N/A", "shared": "N/A",
                              "required": True, "note": "Must provide in-app account deletion option"})

    return {
        "data_declarations": declarations,
        "total_declarations_needed": len(declarations),
        "account_deletion_required": has_account,
        "privacy_policy_required": True,
        "checklist": [
            f"☐ Declare '{d['type']}' in Data Safety form" for d in declarations
        ] + [
            "☐ Add privacy policy URL to store listing",
            "☐ Complete IARC content rating questionnaire",
            "☐ Set correct target audience age group",
        ],
    }


@mcp.tool()
def generate_fix_plan(findings: list) -> dict:
    """
    TOOL 5: Convert raw policy findings into a prioritized, developer-friendly fix plan.
    Groups fixes by urgency (Before Build / Before Submit / Recommended),
    estimates effort, and gives specific code-level instructions.
    Agent calls this after collecting all findings to produce the final action plan.
    """
    immediate = []
    before_submit = []
    recommended = []

    fix_map = {
        "debuggable": {
            "action": "Set android:debuggable=\"false\" in AndroidManifest.xml (or remove — release builds default to false)",
            "effort": "5 min", "urgency": "Before Build"
        },
        "Target SDK": {
            "action": "Update targetSdkVersion to 35 in app/build.gradle",
            "effort": "30 min–2 hrs (test after)", "urgency": "Before Build"
        },
        "signing": {
            "action": "Generate release keystore and sign AAB before upload",
            "effort": "15 min", "urgency": "Before Build"
        },
        "Package name": {
            "action": "Change applicationId in build.gradle to a unique reverse-domain name",
            "effort": "10 min", "urgency": "Before Build"
        },
        "hardcoded": {
            "action": "Move API keys to local.properties or Android Keystore, not source code",
            "effort": "1–2 hrs", "urgency": "Before Build"
        },
        "cleartext": {
            "action": "Replace all http:// URLs with https:// and remove usesCleartextTraffic=true",
            "effort": "30 min", "urgency": "Before Build"
        },
        "Declaration Form": {
            "action": "Submit Permissions Declaration Form in Play Console → App Content",
            "effort": "20 min", "urgency": "Before Submit"
        },
        "Data Safety": {
            "action": "Complete Data Safety form in Play Console → App Content",
            "effort": "30 min", "urgency": "Before Submit"
        },
        "AAB": {
            "action": "Build AAB instead of APK: ./gradlew bundleRelease",
            "effort": "10 min", "urgency": "Before Submit"
        },
        "allowBackup": {
            "action": "Set android:allowBackup=\"false\" in AndroidManifest.xml",
            "effort": "5 min", "urgency": "Recommended"
        },
    }

    for finding in findings:
        if finding.get("severity") in ["PASS", "INFO"]:
            continue
        title = finding.get("title", "")
        category = finding.get("category", "")
        matched = False
        for keyword, fix in fix_map.items():
            if keyword.lower() in title.lower() or keyword.lower() in category.lower():
                entry = {
                    "issue": title,
                    "severity": finding["severity"],
                    "action": fix["action"],
                    "effort": fix["effort"],
                }
                if fix["urgency"] == "Before Build":
                    immediate.append(entry)
                elif fix["urgency"] == "Before Submit":
                    before_submit.append(entry)
                else:
                    recommended.append(entry)
                matched = True
                break
        if not matched:
            entry = {
                "issue": title,
                "severity": finding["severity"],
                "action": finding.get("fix", "Review and fix manually"),
                "effort": "Varies",
            }
            if finding["severity"] == "CRITICAL":
                immediate.append(entry)
            elif finding["severity"] == "HIGH":
                before_submit.append(entry)
            else:
                recommended.append(entry)

    return {
        "total_fixes": len(immediate) + len(before_submit) + len(recommended),
        "before_build": immediate,
        "before_submit": before_submit,
        "recommended": recommended,
        "submission_ready": len(immediate) == 0 and len(before_submit) == 0,
    }


@mcp.resource("playguard://tools-info")
def tools_info() -> str:
    return json.dumps({
        "server": "playguard-agent-tools",
        "pattern": "ReAct — agent reasons and calls tools in a loop until goal is achieved",
        "tools": ["scan_apk", "check_permissions", "generate_store_listing",
                  "build_data_safety_checklist", "generate_fix_plan"],
    })


# ── Internal helpers (not exposed as MCP tools) ───────────────

def _parse_apk(filepath: str) -> dict:
    ext = Path(filepath).suffix.lower()
    result = {
        "package_name": None, "app_name": None,
        "version_code": None, "version_name": None,
        "min_sdk": None, "target_sdk": None,
        "permissions": [], "activities": [], "services": [],
        "is_aab": ext == ".aab", "is_signed": False,
        "dex_strings": [], "debuggable": None,
        "allow_backup": None, "cleartext_traffic": None,
    }
    if HAS_ANDROGUARD:
        try:
            apk = AndroAPK(filepath)
            result.update({
                "package_name": apk.get_package(),
                "app_name": apk.get_app_name(),
                "version_code": apk.get_androidversion_code(),
                "version_name": apk.get_androidversion_name(),
                "min_sdk": apk.get_min_sdk_version(),
                "target_sdk": apk.get_target_sdk_version(),
                "permissions": [str(p) for p in (apk.get_permissions() or [])],
                "activities": list(apk.get_activities() or []),
                "services": list(apk.get_services() or []),
                "debuggable": apk.get_attribute_value("application", "debuggable"),
                "allow_backup": apk.get_attribute_value("application", "allowBackup"),
                "cleartext_traffic": apk.get_attribute_value("application", "usesCleartextTraffic"),
            })
        except Exception:
            pass
    try:
        with zipfile.ZipFile(filepath, 'r') as z:
            names = z.namelist()
            sig = [n for n in names if n.startswith("META-INF/") and n.endswith(('.SF', '.RSA', '.DSA', '.EC'))]
            result["is_signed"] = len(sig) > 0
            for name in names:
                if name.startswith('classes') and name.endswith('.dex'):
                    data = z.read(name)
                    strings = re.findall(b'[\x20-\x7e]{6,}', data)
                    result["dex_strings"].extend([s.decode('ascii', errors='ignore') for s in strings[:2000]])
    except Exception:
        pass
    return result


def _run_checks(meta: dict) -> list:
    findings = []

    def add(cat, sev, title, desc, fix=""):
        findings.append({"category": cat, "severity": sev, "title": title, "description": desc, "fix": fix})

    # Signing
    if meta["is_signed"]:
        add("Signing", "PASS", "App is signed ✅", "Release keystore detected.")
    else:
        add("Signing", "CRITICAL", "App is NOT signed", "Google Play rejects unsigned builds.",
            "Sign with release keystore before upload")

    # AAB vs APK
    if meta["is_aab"]:
        add("Build Format", "PASS", "AAB format ✅", "Correct format for Play Store.")
    else:
        add("Build Format", "MEDIUM", "APK format — AAB preferred",
            "Google requires AAB for new apps since Aug 2021.", "Build AAB: ./gradlew bundleRelease")

    # Target SDK
    sdk = meta.get("target_sdk")
    if sdk:
        try:
            sdk_int = int(sdk)
            if sdk_int < 34:
                add("Target SDK", "CRITICAL", f"Target SDK {sdk_int} below minimum (34)",
                    "Apps targeting below API 34 are rejected.", "Set targetSdkVersion 35 in build.gradle")
            elif sdk_int >= 35:
                add("Target SDK", "PASS", f"Target SDK {sdk_int} ✅", "Meets requirements.")
            else:
                add("Target SDK", "LOW", f"Target SDK {sdk_int} — upgrade to 35 recommended",
                    "API 34 accepted but 35 is recommended.", "Upgrade to targetSdkVersion 35")
        except (ValueError, TypeError):
            pass

    # Permissions
    perms = meta.get("permissions", [])
    critical_p = [p for p in perms if p in CRITICAL_PERMS]
    high_p = [p for p in perms if p in HIGH_PERMS]
    if critical_p:
        add("Permissions", "CRITICAL", f"{len(critical_p)} critical permission(s)",
            "Require Declaration Form:\n" + "\n".join(critical_p),
            "Submit Permissions Declaration Form in Play Console")
    if high_p:
        add("Permissions", "HIGH", f"{len(high_p)} high-risk permission(s)",
            "\n".join(high_p), "Justify each permission in Play Console")
    if not critical_p and not high_p:
        add("Permissions", "PASS", "Permission profile clean ✅", f"{len(perms)} permissions, no critical ones.")

    # Debuggable
    if str(meta.get("debuggable") or "").lower() == "true":
        add("Security", "CRITICAL", "android:debuggable=true",
            "Production builds must not be debuggable.", "Remove or set to false in AndroidManifest.xml")
    else:
        add("Security", "PASS", "Not debuggable ✅", "Correct for production.")

    # Cleartext
    if str(meta.get("cleartext_traffic") or "").lower() == "true":
        add("Security", "HIGH", "usesCleartextTraffic=true",
            "Allows unencrypted HTTP.", "Use HTTPS everywhere; remove cleartext flag")

    # Hardcoded secrets
    strings_text = " ".join(meta.get("dex_strings", []))
    secrets = re.findall(r'AIza[A-Za-z0-9_\-]{35}|AKIA[A-Z0-9]{16}|api[_\-]?key["\s]*[:=]["\s]*[A-Za-z0-9_\-]{16,}', strings_text, re.I)
    if secrets:
        add("Security", "CRITICAL", f"Hardcoded secrets detected ({len(secrets)})",
            f"Found: {secrets[0][:40]}...", "Move secrets to Android Keystore or server-side")
    else:
        add("Security", "PASS", "No hardcoded secrets ✅", "No obvious API keys in DEX strings.")

    # Package name
    pkg = meta.get("package_name") or ""
    if pkg:
        if any(pkg.startswith(b) for b in ["com.example", "com.test", "com.demo", "com.myapp"]):
            add("App Identity", "CRITICAL", f"Default package name: {pkg}",
                "Template names are rejected.", "Change to com.yourcompany.appname")
        elif re.match(r'^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$', pkg):
            add("App Identity", "PASS", f"Package name valid: {pkg} ✅", "Format is correct.")

    return findings


if __name__ == "__main__":
    mcp.run(transport="stdio")
