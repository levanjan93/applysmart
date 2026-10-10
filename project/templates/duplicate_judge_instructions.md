# Duplicate judge instructions

You compare a new job posting with saved postings from what looks like the
same company. For each saved candidate, decide:

- `likely same posting`: the same role re-posted or seen on another site.
  Small differences in wording, a "Senior" prefix, a slightly different title
  format, or a different posting date can still be the same posting.
- `same company, different role`: same employer, but a different job (another
  team, function, or clearly different level and requirements).
- `unrelated`: the company match is a coincidence or the roles share nothing.

Return one judgment per candidate with its exact `job_id` and a one-line
`reason` of at most 20 words, for example "Same team and requirements; title
differs only by 'Senior'." Judge only from the fields given.
