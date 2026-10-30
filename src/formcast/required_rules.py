"""The rules for telling a required field from an optional one, shared by every prompt that classifies.

A module of its own, importing nothing, so the extraction, page-pass and agent prompts can all import it without
a cycle; :mod:`formcast.data.ai` re-exports it.
"""

#: How to tell a required field from an optional one, for any prompt that designs fields or boxes of a form. Each prompt
#: adds its own line on how to reply (the schema's "required" lists and an :data:`~formcast.schema.REASON_KEY` per
#: property; the page pass's list of required boxes).
REQUIRED_RULES = """\
Decide for each field (or box) of the form whether it is required or optional, by these rules. Rule 3 outranks rules
1 and 2: apply it first, and apply the others only to what it leaves.
1. What the form says decides first. Wording such as "required", "an entry is required" or "must", or an asterisk
   the form explains as meaning required, makes it required. Wording such as "optional", "if any", "if applicable"
   or "if different", or "(optional)", makes it optional.
2. When the form is silent, use its structure and context. The fields that identify who or what the form is about
   and the answers the form exists to collect (a name on the first line, a date beside a signature) are required;
   supplementary, alternative and follow-up fields are optional.
3. A field that is one of several alternatives (one may be blank when another is given), or that is needed only
   under a condition (only when another answer is a certain one), is optional, even when the form says "must" or
   "required" over it. When such wording covers a group of alternatives (any one of them satisfies it) or binds only
   some of the people or cases filling the form, every field in it is optional; the reason quotes the wording and
   names the alternatives or the condition.
4. When unsure, it is optional; put what is unclear in its reason.
"""
