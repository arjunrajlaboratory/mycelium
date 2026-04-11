# Skill Packs

External skill repositories cloned here for use by the `skill-bridge` convention pack. These are **inert reference libraries** — never installed as agent skill packs.

## Setup

```bash
cd skillpacks/
git clone https://github.com/K-Dense-AI/scientific-agent-skills.git
git clone https://github.com/bio-skills/bioSkills.git
git clone https://github.com/arjunrajlaboratory/Autonomous-Science.git
```

## Updating

```bash
cd skillpacks/scientific-agent-skills && git pull
cd ../bioSkills && git pull
cd ../Autonomous-Science && git pull
```

## How These Are Used

The `skill-bridge` convention pack (in `network/conventions/skill-bridge/`) routes analysis workflows to specific SKILL.md files within these repos. The agent reads one file at a time (~150-200 lines per analysis step), never loading the full repos into context.

See `network/conventions/skill-bridge/analysis-conventions.md` for the routing hub.
