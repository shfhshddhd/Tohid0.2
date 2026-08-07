---
name: AI mode isolation
description: Separation and safety boundaries for the Telegram AI reply mode
---

AI mention replies must remain independently controlled from the Saved Messages monitoring bridge. AI mode state and its bounded conversation memory should not be changed by `/boton`, `/botoff`, or target-mapping cleanup.

**Why:** Users need to pause either behavior without unexpectedly disabling the other or deleting conversational context.

**How to apply:** Keep AI listener lifecycle, persistence, and cleanup separate from bridge monitoring handlers; scope memory to the hosted owner, group, and participant.