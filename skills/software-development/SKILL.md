---
name: software-development
description: "Software development best practices: debugging, planning, testing, code quality."
version: 1.0.0
author: Open-Agent
triggers:
  - code
  - develop
  - function
  - class
  - refactor
  - review
  - programming
---

# Software Development Skills

## Core Skills

Open-Agent includes several software development skills:

| Skill | Purpose | Triggers |
|-------|---------|----------|
| `systematic-debugging` | Root cause debugging | bug, fix, error, fails |
| `writing-plans` | Implementation planning | plan, implement, build |
| `test-driven-development` | TDD methodology | test, pytest, assert |

## Usage

When you need debugging help:
- Say "debug this error" → loads systematic-debugging skill

When you need planning help:
- Say "plan the implementation" → loads writing-plans skill

When you need testing help:
- Say "write a test" → loads test-driven-development skill

## Key Principles

1. **Debug systematically** — find root cause before fixing
2. **Plan first** — executable steps, not vague goals  
3. **Test driven** — define behavior before implementation
4. **Refactor continuously** — clean as you go
