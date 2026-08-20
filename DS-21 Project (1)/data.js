// Demo data — B2B SaaS PM persona.
window.DEMO = {
  user: { name: "Maya Chen", email: "maya@northgrid.co", role: "Senior PM, Northgrid" },

  // Inbox rows shown in the neutral webmail
  inbox: [
    {
      id: "t1", from: "Priya Ramaswamy", subject: "Re: Q3 OKR review — pricing experiment results",
      preview: "Quick read on the experiment — conversion lifted 7.4% but ARPU stayed flat. I think we should…",
      time: "9:42", reply: "yes", commitment: true, unread: true, label: "Action", attach: false,
    },
    {
      id: "t2", from: "Daniel Okafor", subject: "Contract redlines — Acme renewal",
      preview: "Legal pushed back on Section 4.2. Attached the latest redline; can we discuss the indemnity clause…",
      time: "9:21", reply: "yes", attach: true, unread: true, label: "Action",
    },
    {
      id: "t3", from: "Sarah Kim", subject: "Coffee next week? I'll be in SF Tue–Thu",
      preview: "Hey Maya — would love to catch up if you have 30 mins. I'm around Tuesday afternoon or Thursday…",
      time: "8:58", reply: "yes", unread: true, label: "Ask",
    },
    {
      id: "t4", from: "Stripe", subject: "Your invoice for May 2026 is ready",
      preview: "Invoice #INV-39281 — $1,420.00 — paid automatically on May 12, 2026. Download PDF or view in…",
      time: "Tue", reply: "fyi", attach: true, label: "FYI",
    },
    {
      id: "t5", from: "Linear Updates", subject: "Weekly: 14 issues completed, 3 in review",
      preview: "Your team shipped 14 issues this week, including the new triage view and a major fix to the…",
      time: "Tue", reply: "news", label: "FYI",
    },
    {
      id: "t6", from: "Marcus Liu", subject: "Re: Roadmap doc — comments inside",
      preview: "Left some comments on the Q4 doc. Mostly small but the staffing assumption on row 18 needs a…",
      time: "Mon", reply: "optional", label: "FYI",
    },
    {
      id: "t7", from: "Notion Team", subject: "What's new in Notion — May edition",
      preview: "AI improvements, calendar integrations, and a new permissions model rolling out this week…",
      time: "Mon", reply: "news", label: "Social",
    },
    {
      id: "t8", from: "Ana Beltrán", subject: "Customer escalation: Helix Health",
      preview: "Their integration broke after the schema migration on Friday. CSM is on it but they want a PM…",
      time: "Mon", reply: "yes", unread: true, commitment: true, label: "Action",
    },
    {
      id: "t9", from: "GitHub", subject: "[northgrid/api] PR #4821 needs review",
      preview: "Daniel Okafor requested your review on northgrid/api#4821 — schema migration revert plan…",
      time: "Sun", reply: "optional", label: "FYI",
    },
    {
      id: "t10", from: "Recruiting at Northgrid", subject: "Offer accepted — Jordan Reyes (Sr. PM)",
      preview: "Jordan accepted the offer this morning. Start date confirmed for June 3. HR will send onboarding…",
      time: "Sun", reply: "fyi", label: "FYI",
    },
  ],

  // The "open thread" — Priya
  openThread: {
    id: "t1",
    subject: "Q3 OKR review — pricing experiment results",
    summary: "Priya's pricing test moved conversion 7.4% but ARPU stayed flat. She's asking whether to expand the experiment or roll back, and wants your read by Friday.",
    keyPoints: [
      "Conversion +7.4% on Tier-2 plan (n=2,400)",
      "ARPU flat — discount eating the lift",
      "Decision needed by Fri to slot into Q4 plan",
    ],
    messages: [
      { from: "Priya Ramaswamy", time: "Tue 4:12pm",
        body: "Hey Maya — sharing the Q3 pricing experiment results. Top-line: conversion is up but ARPU isn't moving. Full doc attached. Curious how you'd frame this for the Friday review." },
      { from: "Maya Chen", time: "Tue 5:48pm",
        body: "Thanks Priya — taking a look tonight. I'll send notes tomorrow AM and we can land on a recommendation before the review." },
      { from: "Priya Ramaswamy", time: "today 9:42am",
        body: "Quick read on the experiment — conversion lifted 7.4% but ARPU stayed flat. I think we should hold the rollout and try a narrower discount band. Wanted your take before Friday's review — happy to chat for 15 mins today." },
    ],
    commitments: [
      { text: "Send pricing notes to Priya by Wed AM", due: "tomorrow", origin: "you, Tue 5:48pm", status: "pending" },
    ],
    related: [
      { subject: "Pricing experiment v1 — results", date: "Mar 4", from: "Priya Ramaswamy" },
      { subject: "Q3 OKRs — pricing & growth", date: "Feb 18", from: "Daniel Okafor" },
    ],
  },

  // Commitments across all threads
  commitments: [
    { id: "c1", text: "Send pricing notes to Priya", to: "Priya Ramaswamy",
      thread: "Q3 OKR review — pricing experiment results", due: "tomorrow", status: "pending" },
    { id: "c2", text: "Draft response on indemnity clause", to: "Daniel Okafor",
      thread: "Contract redlines — Acme renewal", due: "Fri May 15", status: "pending" },
    { id: "c3", text: "Review staffing plan in roadmap doc", to: "Marcus Liu",
      thread: "Roadmap doc — comments inside", due: "Wed May 13", status: "pending" },
    { id: "c4", text: "Reply to Helix Health on integration fix ETA", to: "Ana Beltrán",
      thread: "Customer escalation: Helix Health", due: "today", status: "overdue" },
    { id: "c5", text: "Schedule coffee with Sarah Kim", to: "Sarah Kim",
      thread: "Coffee next week?", due: "Thu May 14", status: "pending" },
    { id: "c6", text: "Approve Jordan Reyes onboarding plan", to: "Recruiting",
      thread: "Offer accepted — Jordan Reyes", due: "May 18", status: "pending" },
  ],

  // Email debt buckets — week-by-week
  debt: { current: 23, week: [9,12,8,15,18,21,23] },

  // Draft variants
  drafts: [
    {
      tone: "Brief & direct", length: "≈ 38 words",
      body: "Agree — let's hold the rollout. The flat ARPU is the signal; we shouldn't ship a discount that doesn't lift revenue. Try a narrower band (5–10%) and re-test against the Tier-2 cohort. Free 4pm today to align."
    },
    {
      tone: "Warm & detailed", length: "≈ 84 words",
      body: "Thanks for the quick read, Priya. I'm with you — flat ARPU is the part I can't get past, and shipping a wider discount without revenue lift sets a precedent I'd rather not.\n\nWhat I'd propose: hold the broad rollout, narrow the discount band to 5–10%, and rerun against Tier-2 for another two weeks. That gives us a cleaner signal before Friday.\n\nI have 4–4:30 today open if you want to talk it through before I write it up."
    },
    {
      tone: "Formal & cautious", length: "≈ 62 words",
      body: "Thanks for sharing, Priya. Before recommending a path for Friday, I'd like to confirm the ARPU baseline and segment the conversion lift by plan size. Pending that, my inclination is to hold the broad rollout and propose a narrower discount band for re-test. Could we schedule 15 minutes today or tomorrow morning to align on the framing?"
    },
  ],

  // Attachment summary (open thread version)
  attachments: [
    {
      name: "Q3-pricing-experiment-results.pdf", kind: "pdf", size: "1.4 MB",
      summary: "12-page memo covering the Q3 pricing experiment. Key finding: conversion lifted 7.4% on Tier-2 but ARPU stayed flat because the discount fully absorbed the lift. Recommendation: hold broad rollout, retest with narrower discount band.",
      extracts: [
        { k: "Test window", v: "Mar 4 – Apr 21, 2026" },
        { k: "Sample", v: "n = 2,400 (Tier-2 cohort)" },
        { k: "Conversion lift", v: "+7.4% (p < 0.01)" },
        { k: "ARPU lift", v: "+0.3% (not significant)" },
        { k: "Recommendation", v: "Hold rollout, retest narrower band" },
      ],
    },
  ],

  // Action log entries
  actions: [
    { id: "a1", verb: "Drafted reply", target: "Re: Q3 OKR review — pricing experiment", time: "just now", reversible: true, kind: "open" },
    { id: "a2", verb: "Archived",      target: "Linear Updates — Weekly digest",          time: "2 min ago", reversible: true },
    { id: "a3", verb: "Labeled",       target: "Stripe invoice → FYI",                   time: "8 min ago", reversible: true },
    { id: "a4", verb: "Snoozed",       target: "Notion Team — What's new",               time: "12 min ago", reversible: true },
    { id: "a5", verb: "Marked complete", target: "Commitment: send Q2 readout to Daniel", time: "32 min ago", reversible: true },
  ],
};
