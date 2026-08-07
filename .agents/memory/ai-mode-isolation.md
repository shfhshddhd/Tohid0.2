---
name: AI mode isolation
description: Separation and safety boundaries for the Telegram AI reply mode
---

AI mention replies must remain independently controlled from the Saved Messages monitoring bridge. AI mode state and its bounded conversation memory should not be changed by `/boton`, `/botoff`, or target-mapping cleanup.

**Why:** Users need to pause either behavior without unexpectedly disabling the other or deleting conversational context.

**How to apply:** Keep AI listener lifecycle, persistence, and cleanup separate from bridge monitoring handlers; scope memory to the hosted owner, group, and participant.

The AI persona may be cold, confident, concise, Roman-alphabet-only, uppercase, and free of emojis/full stops, but it must not impersonate a human or escalate into threats, hate, or targeted abuse.

**Why:** Persona customization should not override transparency and safety boundaries.

**How to apply:** Put these boundaries in the system instruction and enforce the output format with a final sanitizer.