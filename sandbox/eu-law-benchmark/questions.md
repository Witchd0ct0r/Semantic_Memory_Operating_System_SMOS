# EU-law benchmark — question set

10 questions, each requiring synthesis across Regulation (EU) 2018/1807 AND GDPR — no question
is answerable from GDPR alone (threat-to-validity mitigation, design spec §7: GDPR alone is
too well-known to any LLM to test retrieval; Regulation 2018/1807 is the low-memorization
anchor). Reference answers grounded in the real EUR-Lex text, not shown to either subagent —
used only for scoring in Task 6.

## Q1 — Pseudonymised identifiers in a mixed dataset
**Articles:** Reg 2018/1807 Art 2(2), Art 3(1); GDPR Art 4(1), Art 4(5)
**Question:** A company's dataset mixes anonymized sensor readings with pseudonymised user
identifiers that the company can still re-link via a separate key it holds. Regulation
2018/1807 Article 3(1) defines "data" (its own scope) as data other than personal data as
defined in GDPR Article 4(1); GDPR Article 4(5) defines pseudonymisation as processing personal
data so it can no longer be attributed to a data subject "without the use of additional
information." Given the company still holds that additional information, does the
pseudonymised-identifier portion of the dataset count as 2018/1807's "data," and which regime
governs the dataset as a whole?
**Reference answer:** Because the company retains the key needed to re-link the identifiers to
individuals, GDPR Article 4(5)'s own definition means the pseudonymised identifiers remain
"personal data" under GDPR Article 4(1) — pseudonymisation doesn't remove personal-data status,
it's still attributable via additional information the company holds. So the identifiers fall
outside 2018/1807 Article 3(1)'s definition of "data" (which explicitly excludes personal
data). Since the sensor readings and the identifiers are inextricably linked in one dataset,
Article 2(2)'s second sentence applies: 2018/1807 "shall not prejudice the application of
Regulation (EU) 2016/679" — GDPR governs the whole linked dataset, not just 2018/1807's
non-personal-data rules.

## Q2 — Scope of the data-localisation prohibition
**Articles:** Reg 2018/1807 Art 3(1), Art 4(1); GDPR Art 4(1)
**Question:** Regulation 2018/1807 Article 4(1) prohibits Member State data-localisation
requirements unless justified by public security, but only for "data" as Article 3(1) defines
the term. If a Member State law instead requires in-country storage specifically for datasets
containing personal data (as GDPR Article 4(1) defines it), is that law covered by Article
4(1)'s prohibition?
**Reference answer:** No. Article 3(1) defines "data" for 2018/1807's purposes as data other
than personal data under GDPR Article 4(1); Article 4(1)'s prohibition is scoped to that same
defined term. A localisation requirement aimed at personal data falls outside 2018/1807
entirely — it isn't a "data localisation requirement" this Regulation prohibits or permits;
whether it's lawful is a question for GDPR's own rules, not 2018/1807 Article 4.

## Q3 — Competent-authority access vs. the mixed-dataset carve-out
**Articles:** Reg 2018/1807 Art 5(1), Art 2(2)
**Question:** 2018/1807 Article 5(1) says access to "data" by competent authorities may not be
refused merely because it's processed in another Member State. If the requested dataset is a
mixed personal/non-personal dataset with the two parts inextricably linked, does Article
5(1)'s no-refusal guarantee extend to the personal-data portion?
**Reference answer:** Not directly. Article 5(1)'s guarantee is scoped to "data" as Article
3(1) defines it (non-personal data), and Article 2(2) states that for inextricably-linked mixed
data, 2018/1807 "shall not prejudice" GDPR. So the personal-data portion of the linked dataset
is governed by GDPR's own access/legal-basis rules, not by Article 5(1)'s guarantee — a
competent authority seeking that portion needs a GDPR-compliant basis, separate from (and not
automatically granted by) 2018/1807's Article 5(1).

## Q4 — Porting codes of conduct vs. the personal-data portion
**Articles:** Reg 2018/1807 Art 6(1)(a), Art 2(2); GDPR Art 1
**Question:** 2018/1807 Article 6(1)(a) encourages self-regulatory codes of conduct for porting
data between cloud service providers in machine-readable formats. If a professional user asks
a provider to port a mixed dataset under such a code, does that code of conduct, on its own,
satisfy the provider's obligations for the personal-data portion of that dataset?
**Reference answer:** No. Article 6's codes of conduct are a 2018/1807 mechanism aimed at data
as Article 3(1) defines it (non-personal data); GDPR Article 1 states GDPR's own subject matter
is "rules relating to the protection of natural persons with regard to the processing of
personal data" — a separate, mandatory legal regime a voluntary porting code can't substitute
for. Article 2(2)'s "shall not prejudice" language confirms 2018/1807's porting mechanism
doesn't override GDPR obligations attaching to the personal-data portion.

## Q5 — Cross-border cooperation channel vs. GDPR's own territorial regime
**Articles:** Reg 2018/1807 Art 7, Art 2(2); GDPR Art 3
**Question:** 2018/1807 Article 7 sets up a single-point-of-contact procedure for one Member
State's competent authority to request non-personal data held in another Member State. Is this
the right channel for a competent authority seeking the personal-data portion of a mixed
dataset from a controller established in another Member State?
**Reference answer:** No — Article 7's procedure is scoped to "data" under Article 3(1)
(non-personal data); per Article 2(2), the personal-data portion of an inextricably-linked
mixed dataset stays under GDPR. GDPR Article 3 already establishes its own territorial-scope
rules for controllers/processors established in the EU (or targeting EU data subjects)
regardless of where processing occurs — that GDPR framework, not 2018/1807 Article 7's
cooperation procedure, governs access to the personal-data portion.

## Q6 — Public-security exception doesn't cross the personal/non-personal boundary
**Articles:** Reg 2018/1807 Art 4(1); GDPR Art 2
**Question:** 2018/1807 Article 4(1) allows a Member State to keep a data-localisation
requirement for non-personal data if justified on public-security grounds. Does an equivalent
public-security carve-out remove the personal-data portion of a mixed dataset from GDPR's own
material scope?
**Reference answer:** No — GDPR Article 2's material-scope exclusions are narrow (e.g., purely
personal/household processing) and do not include a general public-security carve-out matching
2018/1807 Article 4(1)'s. So even where a public-security justification lets a Member State
keep a localisation rule for the non-personal part under 2018/1807, the personal-data portion
of the same mixed dataset stays fully within GDPR's material scope — the two Regulations'
public-security/exclusion logic doesn't transfer across the personal/non-personal boundary.

## Q7 — Why deanonymisation risk matters to a "non-personal data" Regulation
**Articles:** Reg 2018/1807 Art 8(1)(a); GDPR Art 4(5)
**Question:** 2018/1807 Article 8(1)(a) requires the Commission to evaluate the Regulation's
application to mixed datasets "considering deanonymisation risks." Why would deanonymisation
risk matter for a Regulation that, per Article 3(1), only ever applies to non-personal data in
the first place?
**Reference answer:** Because whether data is genuinely "non-personal" (and thus within
2018/1807's own scope) depends on whether it can be re-attributed to a person — the same
question GDPR Article 4(5)'s pseudonymisation definition turns on ("can no longer be attributed
to a specific data subject without the use of additional information"). Data companies treat as
anonymised/non-personal under 2018/1807 may in fact be re-identifiable, meaning it was
mischaracterized and should have been treated as personal data under GDPR all along; Article
8(1)(a)'s review exists to check how large that mischaracterization risk is in practice.

## Q8 — What the mandated GDPR-interaction guidance adds beyond Article 2(2)
**Articles:** Reg 2018/1807 Art 2(2), Art 8(1)(a), Art 8(3)
**Question:** Article 2(2) already states the substantive rule for mixed datasets (non-personal
part under 2018/1807, GDPR unaffected for inextricably-linked personal data). Article 8(3)
separately requires the Commission to publish guidance on "the interaction of this Regulation
and Regulation (EU) 2016/679, especially as regards data sets composed of both personal and
non-personal data" by 29 May 2019. What open question is that guidance meant to resolve, given
Article 2(2) already sets the rule?
**Reference answer:** Article 2(2) states the rule but not how to apply it in practice —
determining whether a given dataset's personal and non-personal parts are "inextricably
linked" (versus separable) is exactly the practical, fact-specific judgment call Article
8(1)(a)'s deanonymisation-risk review and Article 8(3)'s interaction guidance are meant to help
make; the Articles establish that a boundary exists and who it defers to (GDPR, for linked
data), not how to draw it for a specific dataset.

## Q9 — Two independently-triggered territorial regimes for one dataset
**Articles:** Reg 2018/1807 Art 1, Art 2(1); GDPR Art 3
**Question:** A non-EU cloud provider with no EU establishment stores a mixed dataset for EU
business customers, offering its storage service to those Union-based users. GDPR Article 3
extends GDPR to non-EU entities that offer goods or services to, or monitor, data subjects in
the Union. Does 2018/1807 apply an equivalent extraterritorial test to the non-personal part of
the same dataset?
**Reference answer:** Yes, on a similar footing — 2018/1807 Article 2(1) scopes the Regulation
to processing of non-personal data in the Union carried out as a service to users located in
the Union (as well as by persons established in the Union), so a non-EU provider serving
Union-based business customers falls within 2018/1807's own scope for the non-personal part, on
a service-to-EU-users basis analogous to (but a legally separate test from) GDPR Article 3's
offering-goods-or-services trigger for the personal-data part. The same dataset can therefore be
split across two independently-triggered territorial regimes.

## Q10 — Repeal deadline vs. the mixed-dataset carve-out
**Articles:** Reg 2018/1807 Art 4(3), Art 2(2)
**Question:** Article 4(3) requires Member States to repeal existing data-localisation
requirements that don't meet Article 4(1)'s public-security justification, by 30 May 2021. Does
that repeal deadline apply to a Member State law requiring in-country storage of a dataset that
is an inextricably-linked mix of personal and non-personal data?
**Reference answer:** Not on its own terms. Article 4's repeal obligation runs against
localisation requirements for "data" under Article 3(1) — i.e., non-personal data. Because
Article 2(2) excludes inextricably-linked mixed data from 2018/1807's substantive rules (leaving
it to GDPR), a law requiring in-country storage of such a mixed dataset isn't a "data
localisation requirement" 2018/1807 Article 4 reaches at all, so the 30 May 2021 repeal deadline
doesn't apply to it by virtue of this Regulation — its lawfulness (if any) would need to be
assessed under GDPR or other applicable law instead.
