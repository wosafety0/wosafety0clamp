# Formation Rules

**Status:** PARTIAL — community-source research; not yet live-verified against the current Poi session  
**Last reviewed:** 2026-11-06  
**Primary sources:** Japanese Kancolle Wiki, [陣形](https://wikiwiki.jp/kancolle/陣形) and [連合艦隊](https://wikiwiki.jp/kancolle/連合艦隊), accessed 2026-11-06.

## Rule records

### FORM-001 — Standard formation selection threshold
- **Rule:** The source states that a fleet with four or more ships can select a formation before an engagement; a fleet with three or fewer ships automatically uses Line Ahead (単縦陣). Circle (輪形陣) requires at least five ships.
- **Evidence:** COMMUNITY_REPORTED.
- **Source:** [陣形](https://wikiwiki.jp/kancolle/陣形), lines 3–11 and 15–20.
- **Automation consequence:** Do not show or wait for a formation selector when the observed fleet size or node type does not support it. Current UI behavior still needs Poi verification.

### FORM-002 — Standard formation set
- **Rule:** The source lists five basic formations: Line Ahead (単縦陣), Double Line (複縦陣), Diamond (輪形陣), Echelon (梯形陣), and Line Abreast (単横陣). Special formations such as Vanguard (警戒陣) and Combined Fleet formations require separate conditions.
- **Evidence:** COMMUNITY_REPORTED.
- **Source:** [陣形](https://wikiwiki.jp/kancolle/陣形), lines 6–11.
- **Automation consequence:** Do not assume every formation is always available. Treat special formations as separately gated features.

### FORM-003 — Formation choice changes battle modifiers
- **Rule:** The source states that formations apply different battle modifiers and describes different strengths and trade-offs. It warns that numeric values include community testing and may be uncertain.
- **Evidence:** COMMUNITY_REPORTED.
- **Source:** [陣形](https://wikiwiki.jp/kancolle/陣形), accessed 2026-11-06.
- **Automation consequence:** A tactical policy cannot select formations from names alone. It needs the relevant enemy composition, battle objective, and verified modifier model. No universal default strategy is established here.

### FORM-004 — Combined Fleet uses a separate formation system
- **Rule:** Combined Fleets have special formations documented separately from standard fleet formations.
- **Evidence:** COMMUNITY_REPORTED.
- **Source:** [陣形](https://wikiwiki.jp/kancolle/陣形), lines 8–11; [連合艦隊](https://wikiwiki.jp/kancolle/連合艦隊).
- **Automation consequence:** Never apply standard-fleet formation IDs or tactical assumptions to a Combined Fleet without a dedicated mapping.

### FORM-005 — Some node types bypass formation selection
- **Rule:** The source states that radar-shooting nodes do not show a formation-selection screen.
- **Evidence:** COMMUNITY_REPORTED.
- **Source:** [陣形](https://wikiwiki.jp/kancolle/陣形), lines 3–5.
- **Automation consequence:** A missing formation screen is not sufficient evidence of a broken UI. Node type must be identified first.

## Unresolved formation rules — implementation prohibited until researched

- Exact numeric modifiers for shelling, torpedo, anti-submarine, air defense, hit, evasion, and flagship protection.
- Special-attack trigger conditions and formation dependencies.
- Vanguard availability, event-only availability, and exact positional behavior.
- Combined Fleet formation IDs, selection mapping, and battle-type restrictions.
- How Poi's UI and passive state data represent the selected formation.

## Implementation gate

Do not implement a tactical formation selector from a generic preference list. Each decision must cite a rule ID and verify that the current fleet type, ship count, node type, and tactical objective satisfy the rule's preconditions.
