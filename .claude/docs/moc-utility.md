---
title: Utility Commands
type: moc
phase: utility
description: Standalone tools available at any time, independent of the main workflow phases.
---

# Utility Commands

These commands operate independently of the four workflow phases ([[moc-understanding]], [[moc-planning]], [[moc-execution]], [[moc-finalization]]). They can be used at any time.

## Commands

[[/skill-creator]] creates or updates skills that extend Claude's capabilities with specialized knowledge, workflows, or tool integrations. Use when you need a new skill or want to improve an existing one.

[[/reflect]] turns agent mistakes, failures, and user corrections into permanent rules. Use after any error, test-failure pattern, hook false positive, or workflow friction worth stabilizing.

[[/context7]] fetches up-to-date, version-specific library/framework documentation via the Context7 CLI (`npx -y ctx7 …`). Use any time you need current API docs instead of training-cutoff knowledge — it runs inline so the docs land in your active context.

## Notes

These are standalone workflows — they do not feed into the next phase and have no `flow-next` connections. After completion, return to whatever phase you were working in.

Unlike `skill-creator` and `reflect` (which fork into their own agents), `context7` runs **inline with no agent**: a forked summary would discard the very docs it fetches, so it loads doc-fetch results straight into the calling skill's context.
