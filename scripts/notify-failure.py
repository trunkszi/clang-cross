#!/usr/bin/env python3
"""Write a failure panel and optionally send it through an explicitly configured SMTP server."""

import email.message, os, pathlib, smtplib, ssl, sys

url = (
    os.getenv("GITHUB_SERVER_URL", "https://github.com")
    + "/"
    + os.getenv("GITHUB_REPOSITORY", "unknown")
    + "/actions/runs/"
    + os.getenv("GITHUB_RUN_ID", "unknown")
)
text = (
    "## Toolchain release failed\n\n"
    + "\n".join(
        "- " + stage + ": **" + os.getenv(key, "unknown") + "**"
        for stage, key in [
            ("Discovery / patches", "PREPARE_RESULT"),
            ("Build / runtime verification", "BUILD_RESULT"),
            ("Publication", "RELEASE_RESULT"),
        ]
    )
    + "\n\nRun diagnostics: "
    + url
    + "\n\nDiscovery, patch, and build failures leave the published release untouched. Upload failures may leave partially replaced assets; inspect the publication log before retrying.\n"
)
print("::error title=TOOLCHAIN_RELEASE_FAILED::See " + url)
path = os.getenv("GITHUB_STEP_SUMMARY")


def append(value):
    if path:
        with pathlib.Path(path).open("a") as f:
            f.write(value)


append(text)
if not os.getenv("SMTP_HOST"):
    append("\nSMTP notification: **NOT SENT** (SMTP_HOST is not configured).\n")
    print("SMTP notification not configured; no email sent.")
    sys.exit(0)
try:
    message = email.message.EmailMessage()
    message["Subject"] = "clang-cross release failure"
    message["From"] = os.environ["SMTP_FROM"]
    message["To"] = os.environ["SMTP_TO"]
    message.set_content(text)
    with smtplib.SMTP(
        os.environ["SMTP_HOST"], int(os.getenv("SMTP_PORT") or "587"), timeout=30
    ) as smtp:
        smtp.starttls(context=ssl.create_default_context())
        if os.getenv("SMTP_USERNAME"):
            smtp.login(os.environ["SMTP_USERNAME"], os.environ["SMTP_PASSWORD"])
        smtp.send_message(message)
    append("\nSMTP notification: **SENT**.\n")
except Exception as error:
    append("\nSMTP notification: **FAILED**; inspect server configuration.\n")
    print(
        "::error title=SMTP_NOTIFICATION_FAILED::" + type(error).__name__,
        file=sys.stderr,
    )
    sys.exit(1)
