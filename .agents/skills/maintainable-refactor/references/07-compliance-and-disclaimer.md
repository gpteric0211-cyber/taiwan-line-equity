# Securities-Analysis Compliance And Disclaimer Rules

Last official-source review: 2026-08-26.

This is an engineering risk-control checklist, not legal advice, a licensing determination, or a safe harbor. A disclaimer or neutral label cannot cure conduct that is regulated, misleading, or unsupported.

## Contents

1. Launch and business-model gate
2. Message classes and disclaimer policy
3. Restricted wording and claims
4. Data-quality wording
5. Personalized-advice boundary
6. Release checklist
7. Official sources

## 1. Launch And Business-Model Gate

Taiwan's Securities Investment Trust and Consulting Act defines securities investment consulting around both providing securities-related analysis/recommendations and directly or indirectly receiving compensation from a client or third party. Audience, content, personalization, advertising, solicitation, remuneration, and operator status all matter.

Before public launch, charging, advertising, affiliate/referral monetization, or third-party-funded distribution:

- Document the operator, target audience, exact outputs, personalization inputs, revenue/remuneration paths, advertising/solicitation, and whether individual securities are scored or recommended.
- Obtain a written review from qualified Taiwan counsel and, where appropriate, a licensed investment-consulting compliance reviewer or regulator-facing adviser.
- Record the reviewer, scope, date, decision, required license/status, approved copy, and conditions.
- Do not call the bot, company, or agent a licensed investment adviser unless that status has been verified.
- If classification is unresolved, fail closed: keep output factual/educational and disable individualized allocation, directive trades, “reasonable price”/target-price recommendations, and promised future-price conclusions.

General market statistics, technical-analysis theory, and public facts may be lower risk, but stock-specific scores, statuses, support/pressure, “next-day outlook,” reasonable prices, or action plans require substance-based review. Renaming a recommendation as a “quantitative signal” or “observation range” does not decide its legal character.

## 2. Message Classes And Disclaimer Policy

The following are product rules, not claims about a legally prescribed disclaimer format:

| Message class | Required output control |
|---|---|
| First use and every terms-version change | Full investment-risk disclaimer, service scope, terms link, and a separate privacy notice/link |
| Stock-specific interpretation, score, status, signal, support/pressure, or outlook | Short disclaimer in the same message/Flex bubble every time |
| Automated alert | Short disclaimer plus the condition, data date, and quality state every time |
| Pure factual quote/volume with no interpretation | Show date/time and quality; apply the legally approved factual-message footer policy |
| Help/menu/non-financial small talk | No investment disclaimer unless financial interpretation is included |

Baseline full copy for legal review, not automatic legal approval:

`本內容為量化資料整理與統計觀察，僅供參考，不構成投資建議，亦不保證獲利或損益。投資有風險，請自行判斷並負擔投資決策之最終責任。`

Baseline short footer for legal review:

`僅供資料整理，不構成投資建議。`

- Keep approved copy in one shared builder/configuration, not duplicated across templates.
- Full terms must remain discoverable at any time; do not define an ambiguous “once per session/day” rule.
- Investment-risk wording and personal-data notice/consent are separate controls. One does not satisfy the other.
- Version approved copy and test exact rendered output.

## 3. Restricted Wording And Claims

Never make affirmative claims of:

- Guaranteed profit, no risk, certain rise/fall, loss reimbursement, “穩賺”, “明牌”, “報牌”, or inside information
- Imperative personalized commands such as “現在買進”, “立刻賣出”, “替你加碼”, or “全部停損”
- A promised future price or a target guaranteed to be reached
- Human/licensed review that did not occur
- Evidence, source quality, or certainty the system does not have

Terms such as “買進” or “目標價” may appear in negation, policy explanations, or accurately quoted user questions; do not use a brittle word-only ban. Evaluate the complete claim.

Technical levels may be described only as method/date-qualified reference ranges, never promises. Inner/outer-volume accumulation output must explicitly say it is an observation signal and not a buy recommendation; semantic-equivalent approved wording is acceptable.

Every stock-specific analysis must show a reasonable analytical basis through traceable internal inputs. Do not output a conclusion whose referee/data-quality evidence is unavailable.

Apply the canonical output-sanitization policy in reference 05.

## 4. Data-Quality Wording

Map the quality states from reference 01 to user wording without overstating certainty:

- `ok`: state the value/status normally with its date and required disclaimer.
- `estimated`: explicitly say `推估` or an approved equivalent; never imply an exchange-confirmed value.
- `stale`: state that the data is older than expected and do not present it as current.
- `source_delayed`: state that the expected source update has not arrived.
- `unavailable` / `missing`: show an honest unavailable/pending state; never fabricate a filler number or conclusion.

## 5. Personalized-Advice Boundary

Without a separately approved legal/licensing phase:

- Do not use a user's assets, income, debts, risk tolerance, taxes, retirement needs, or full portfolio to generate allocation or trade instructions.
- Do not claim individualized suitability or “tailored” advice.
- Asking whether the user already holds a stock solely to select neutral risk-context wording does not authorize personalized financial planning.
- Direct tax, legal, retirement-allocation, or distress-driven leverage questions to an appropriate licensed professional and keep the response within a factual safety boundary.
- Never imply that a model is a human adviser or that a human reviewed an answer when none did.

## 6. Release Checklist

Before shipping a new or changed stock-analysis template:

- [ ] Message class and disclaimer tier identified
- [ ] Approved disclaimer copy rendered in the required location
- [ ] No guaranteed, imperative, personalized, or promised-price claim
- [ ] Analytical basis and single-referee conclusion verified
- [ ] Quality-state wording matches the payload
- [ ] Reference 05 sanitization passes on exact output
- [ ] Terms and privacy notice remain accessible
- [ ] Human legal/compliance sign-off recorded when the launch gate applies

Changing this skill does not retroactively make existing LINE output compliant. Audit existing templates separately before production/public release.

## 7. Official Sources

- [Securities Investment Trust and Consulting Act](https://law.fsc.gov.tw/LawContent.aspx?id=FL030633)
- [Regulations Governing Securities Investment Consulting Enterprises](https://law.fsc.gov.tw/LawContent.aspx?id=GL000529)
- [FSC FinTech regulatory clinic FAQ](https://www.fsc.gov.tw/websitedowndoc?file=chfsc/202507071713120.pdf&filedisplay=%E7%9B%A3%E7%90%86%E9%96%80%E8%A8%BAFAQ.pdf)

Re-check current law and obtain qualified advice before relying on this reference.
