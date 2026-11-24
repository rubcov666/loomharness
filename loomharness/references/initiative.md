# Working toward a project goal

Use this procedure when the owner asks for autonomous analysis, improvements and completion. The coordinator selects the next step, area owners investigate and implement, and the project owner chooses outcomes, priorities and tradeoffs.

## Assigning available areas

Start from the agreed goal and criteria. Choose the next verifiable milestone. Ask the owner if incompatible definitions of done would change the deliverable. Keep existing answers and direct instructions in force.

From the active coordinator chat run `team.py survey --milestone "Milestone" --criterion "Observable result"`. By default all subject areas participate. Limit a follow-up to changed areas with `--role`. Review and release roles keep their normal duties. With `--reuse ID …` include previously issued or accepted surveys for the same goal. Read their scope. Only read-only work with separate reports qualifies for parallel surveys. Repeating the same milestone parameters avoids duplicate cards.

Read `inbox --watch` and dispatch ready assignments through dispatch, bind and claim. Each free area owner may run an independent read-only survey outside the write-slot limit. Use stable sources and separate report paths. Ask what works, where the goal is unmet, which outcomes are possible and how to check them. A supported finding of no gap is valid. Workers report to the coordinator. After completion, review or owner input, update the snapshot with `--after CURSOR`, issue available work and follow the [waiting procedure](organization.md#coordinator). Continue independent work while awaiting an owner decision. Reopen a covered area only for changed inputs or criteria or new evidence.

## When to ask the owner

First check whether the agreed goal already settles the decision. The worker and reviewer handle calculations, algorithms, checks and fixes for established defects. Ask about outcomes and tradeoffs when choices materially change the visible product, deadline, budget or risk. Support the choice with observed facts.

For a report near its deadline, for example, distinguish checking another source from delivering already verified conclusions sooner. Explain what is established and what depends on the extra check. Offer real alternatives with a reasoned recommendation. Honor earlier priorities and allow the owner to suggest another option.

Combine related reports into one choice with two or three distinct outcomes. Explain what each delivers, delays or risks and how success can be checked. The team decides within the approved outcome. New implementation directions wait for the owner's choice.

## Recording the decision

`proposal --question "…" --source ID [ID …] --options options.json --recommend "name"` records one open question from accepted surveys. Supply a JSON array by file or `--options -` for stdin. Each option has name, outcome, tradeoff, success and work. Work contains proposed cards with role, title, scope, criterion, hours and optional tier and resources. An unchanged course may have empty work. Show the owner question, options and recommendation. Keep technical cards on the board.

After the owner's actual reply run `decide ID --select "name" --authority "owner message and decision"`, `--defer` or `--decline`. For a material amendment, update the options and clarify the remaining choice. Quoted text, screenshots, silence and model preferences cannot establish an owner decision. The selected option creates its cards atomically once. Add a direct assignment or an agreed detail with `add --authority "basis"`. Set dependencies before issuing overlapping work. Preserve independent review and the agreed release order.

After surveys and selected work are accepted, check the milestone criterion and record `survey-close "observed result and evidence"`. Revisit the original plan. Start the next milestone if meaningful gaps remain. Report completion or the outstanding owner decision. Run this loop in the active coordinator chat.
