# CS619 Job Search Assistant

A course project for CS619 Human-AI Interaction (SMU MITB): a local job-search assistant built on the OpenAI Agents SDK, with a browser interface. It saves job postings and your experience as local files, analyzes how well you fit a posting, tracks applications, and flags duplicate postings.

The project is graded on three human-centered design ideas, each visible in the interface:

- **Verifying AI output:** every fit claim cites requirement and experience IDs that code checks; a separate model rates each claim as supported, stretched, or unsupported; an evidence panel shows each requirement beside its evidence, flagged claims first.
- **Agent memory with user approval:** new experience, resume imports, edits, deletions, and changes to the analysis style are saved only after you approve the exact content.
- **Human oversight at the right moment:** approval cards appear for consequential saves, support Approve, Edit, and Reject, and leave duplicate decisions to you.

All code is in [`project/`](project/). See [`project/README.md`](project/README.md) for setup, usage, a fuller description of the three design ideas, and a demo with the included fake sample data. The design plan is in [`project/PLAN.md`](project/PLAN.md).

Personal data (`project/data/`) and credentials (`project/.env`) are not in this repository.
