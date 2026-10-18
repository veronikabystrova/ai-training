---
title: Finalization Phase
type: moc
phase: 4
description: Update documentation after code is complete.
---

# Finalization Phase

The fourth phase documents the work from [[moc-execution]] and completes the branch. It updates living documentation, then merges or opens a PR.

## Commands

[[/docs-generator]] generates and maintains project documentation including READMEs, ADRs (Architecture Decision Records), and code documentation (docstrings).

[[/finishing-branch]] completes the branch by presenting structured options for merge, PR, or cleanup. While primarily an [[moc-execution]] command, it is the final step of the phase.

## Flow

The default path is [[/docs-generator]] then [[/finishing-branch]]. If no documentation update is needed, you can go straight to [[/finishing-branch]].

## Outputs

- Updated `README.md`, ADRs in `docs/adr/`, and docstrings

## Previous Phase

Returns from [[moc-execution]] when all tests pass and code review is complete.
