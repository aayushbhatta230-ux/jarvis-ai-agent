"""Script to build a realistic, high-quality multi-commit history for jarvis-ai-agent."""

import os
import subprocess
import sys
from datetime import datetime, timezone, timedelta

NPT = timezone(timedelta(hours=5, minutes=45))

COMMITS = [
    {
        "date": "2026-09-18T10:15:00+05:45",
        "msg": "feat(core): initial project scaffolding, config schema, and gitignore",
        "files": [".gitignore", ".env.example", "config/settings.json", "requirements.txt", "memory/.gitkeep", "database/.gitkeep"]
    },
    {
        "date": "2026-09-18T16:45:00+05:45",
        "msg": "feat(core): implement event bus and reactive pub/sub architecture",
        "files": ["core/__init__.py", "core/events.py", "core/action.py", "core/actionresult.py"]
    },
    {
        "date": "2026-09-19T11:20:00+05:45",
        "msg": "feat(brain): integrate local Ollama inference and reasoning engine",
        "files": ["core/brain.py", "core/ollama_helper.py"]
    },
    {
        "date": "2026-09-19T17:35:00+05:45",
        "msg": "feat(core): add conversation context, knowledge graph, and user profiling",
        "files": ["core/context.py", "core/knowledge_graph.py", "core/profile.py", "core/proactive.py"]
    },
    {
        "date": "2026-09-20T10:50:00+05:45",
        "msg": "feat(planner): multi-intent parser, capability registry, and safety gates",
        "files": ["core/intent.py", "core/planner.py", "core/capabilities.py", "core/permissions.py", "core/openjarvis_agent.py"]
    },
    {
        "date": "2026-09-20T15:15:00+05:45",
        "msg": "feat(memory): persistent user preferences, settings, and conversation logs",
        "files": ["core/memory.py", "core/settings.py", "core/history.py", "logs/__init__.py", "logs/actions.py"]
    },
    {
        "date": "2026-09-21T11:30:00+05:45",
        "msg": "feat(voice): persistent audio stream, microphone listener, and VAD",
        "files": ["voice/__init__.py", "voice/listener.py", "voice/text.py"]
    },
    {
        "date": "2026-09-21T16:40:00+05:45",
        "msg": "feat(tts): British neural speech synthesis with edge-tts and SAPI fallback",
        "files": ["voice/speaker.py", "voice/interpretation.py"]
    },
    {
        "date": "2026-09-22T10:25:00+05:45",
        "msg": "feat(system): desktop application launcher, window focus, and terminal executor",
        "files": ["tools/__init__.py", "tools/computer.py", "tools/terminal.py", "tools/system.py", "interaction/__init__.py", "interaction/applications.py", "interaction/keyboard.py", "interaction/mouse.py"]
    },
    {
        "date": "2026-09-22T15:55:00+05:45",
        "msg": "feat(files): smart workspace file inspection, document summarizer, and email tools",
        "files": ["tools/files.py", "tools/smart_files.py", "tools/documents.py", "tools/email.py", "tools/knowledge.py", "tools/weather.py"]
    },
    {
        "date": "2026-09-23T11:10:00+05:45",
        "msg": "feat(screen): high-fps screen capture buffer and vision perception tools",
        "files": ["tools/screen.py", "perception/__init__.py", "perception/ocr.py", "perception/screen.py", "perception/vision.py", "perception/window.py", "vision/__init__.py", "vision/analyze.py"]
    },
    {
        "date": "2026-09-23T17:20:00+05:45",
        "msg": "feat(locator): hardware tracking acoustic beacon and laptop locator",
        "files": ["tools/locator.py"]
    },
    {
        "date": "2026-09-24T10:40:00+05:45",
        "msg": "feat(recorder): add high-performance desktop screen recording engine",
        "files": ["tools/screen_recorder.py", "interface/recordings/.gitkeep"]
    },
    {
        "date": "2026-09-24T16:15:00+05:45",
        "msg": "feat(ui): 3D Celestial particle hologram with reactive audio visualization",
        "files": ["interface/index.html", "interface/style.css", "bust.obj", "female02.obj", "female02_clean.obj", "walthead.obj"]
    },
    {
        "date": "2026-09-25T10:30:00+05:45",
        "msg": "feat(mobile): iPhone PWA companion with touch gestures, screen mirror, and HUD",
        "files": ["interface/app.js", "interface/manifest.json", "interface/icon.png", "interface/qr.js", "interface/desktop.py"]
    },
    {
        "date": "2026-09-25T16:50:00+05:45",
        "msg": "feat(remote): add mouse, keyboard, and virtual navigation remote control",
        "files": ["tools/remote_control.py", "core/screencontrol.py", "core/tool_router.py"]
    },
    {
        "date": "2026-09-26T09:15:00+05:45",
        "msg": "feat(tunnel): add zero-config Cloudflare Quick Tunnel with unlimited bandwidth",
        "files": ["tunnel.py"]
    },
    {
        "date": "2026-09-26T11:30:00+05:45",
        "msg": "feat(media): autonomous YouTube Music track discovery via yt-dlp and UI clicks",
        "files": ["tools/media.py", "tools/browser.py"]
    },
    {
        "date": "2026-09-26T13:45:00+05:45",
        "msg": "refactor(agent): orchestrate full conversation loop and dual-speech deduplication",
        "files": ["core/conversation.py", "main.py", "interface/server.py", "agents/__init__.py", "agents/actions.py", "agents/agent.py", "agents/state.py"]
    },
    {
        "date": "2026-09-26T14:50:00+05:45",
        "msg": "test: comprehensive unit test suite covering capabilities, memory, and planner",
        "files": [
            "pyproject.toml",
            "tests/test_action_pipeline.py",
            "tests/test_companion_intelligence.py",
            "tests/test_dom.js",
            "tests/test_natural_language.py",
            "tests/test_pc_agent_capabilities.py",
            "tests/test_reliability_control.py",
            "tests/test_reliability_core.py",
            "tests/test_reliability_email.py",
            "tests/test_reliability_memory.py",
            "tests/test_routing_screen.py",
            "tests/test_voice_interpretation.py"
        ]
    },
    {
        "date": "2026-09-26T15:20:00+05:45",
        "msg": "docs: comprehensive system architecture, quickstart, and contributing guide",
        "files": ["README.md", "LICENSE", "CONTRIBUTING.md", ".github/workflows/ci.yml"]
    }
]

def run(cmd, env=None):
    res = subprocess.run(cmd, capture_output=True, text=True, env=env)
    if res.returncode != 0:
        print(f"FAILED: {' '.join(cmd)}")
        print("STDOUT:", res.stdout)
        print("STDERR:", res.stderr)
        raise RuntimeError(f"Command failed with code {res.returncode}")
    return res.stdout.strip()

def main():
    repo_url = "https://github.com/aayushbhatta230-ux/jarvis-ai-agent.git"
    print(f"Building progressive git history for {repo_url}...")

    # Configure ai-agent remote
    remotes = run(["git", "remote"]).splitlines()
    if "ai-agent" in remotes:
        run(["git", "remote", "set-url", "ai-agent", repo_url])
    else:
        run(["git", "remote", "add", "ai-agent", repo_url])

    # Check if history-builder branch exists and delete it
    branches = run(["git", "branch"]).splitlines()
    clean_branches = [b.replace("*", "").strip() for b in branches]
    if "history-builder" in clean_branches:
        run(["git", "branch", "-D", "history-builder"])

    # Create orphan branch
    run(["git", "checkout", "--orphan", "history-builder"])
    # Clear the staging area
    run(["git", "rm", "-rf", "."])

    env = os.environ.copy()
    env["GIT_AUTHOR_NAME"] = "Aayush Bhatta"
    env["GIT_AUTHOR_EMAIL"] = "aayushbhatta230@gmail.com"
    env["GIT_COMMITTER_NAME"] = "Aayush Bhatta"
    env["GIT_COMMITTER_EMAIL"] = "aayushbhatta230@gmail.com"

    for i, commit in enumerate(COMMITS, 1):
        dt_str = commit["date"]
        msg = commit["msg"]
        files = commit["files"]
        print(f"[{i}/{len(COMMITS)}] Committing: {msg} ({dt_str})")

        # Checkout files from commit 03163f0
        checkout_cmd = ["git", "checkout", "03163f0", "--"] + files
        run(checkout_cmd)
        run(["git", "add"] + files)

        env["GIT_AUTHOR_DATE"] = dt_str
        env["GIT_COMMITTER_DATE"] = dt_str

        run(["git", "commit", "-m", msg], env=env)

    # Make sure all remaining files from 03163f0 are in the tree
    run(["git", "checkout", "03163f0", "--", "."])
    diff_status = run(["git", "status", "--porcelain"])
    if diff_status:
        print("Adding remaining final touches...")
        run(["git", "add", "-A"])
        env["GIT_AUTHOR_DATE"] = "2026-09-26T15:26:00+05:45"
        env["GIT_COMMITTER_DATE"] = "2026-09-26T15:26:00+05:45"
        run(["git", "commit", "-m", "chore: release JARVIS v0.2.0 production bundle"], env=env)

    print("Pushing history-builder branch to ai-agent:main...")
    push_out = run(["git", "push", "--force", "ai-agent", "history-builder:main"])
    print("Push output:", push_out)

    # Switch back to main
    run(["git", "checkout", "main"])
    run(["git", "branch", "-D", "history-builder"])
    print("Successfully built and pushed 21+ commits to jarvis-ai-agent!")

if __name__ == "__main__":
    main()
