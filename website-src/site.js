
/* ------------------------------------------------------------------
   Content. Every thread carries its own summary, draft and metadata,
   so the panel says something true about whichever row you pick.
------------------------------------------------------------------- */
const THREADS = [
  { from:"Hartley & Vance LLP", pri:"high", act:true, cat:"legal", catName:"Legal", imp:true, unread:true,
    subj:"Master services agreement: redlines for your review",
    snip:"Marked up clauses 7, 11 and 14. The indemnity cap is the open item.",
    short:"Master services agreement redlines", time:"12:13", meta:"From 6 messages · 2 attachments",
    sum:["Counsel returned redlines on clauses 7, 11 and 14. The indemnity cap is the open item.",
         "They need your position on the cap before Thursday's signing window.",
         "Finance already approved the payment terms in a separate thread."],
    draft:"Thanks for turning these around. We can take 7 and 11 as drafted. On 14, we would want the indemnity cap held at twelve months of fees rather than six. I will confirm with Finance today and come back to you before Thursday." },

  { from:"People Ops", pri:"high", act:true, cat:"hr", catName:"People", imp:true, unread:true,
    subj:"Q3 review cycle opens Monday: manager actions",
    snip:"Six direct reports to write up before the 29th.",
    short:"Q3 review cycle, manager actions", time:"11:46", meta:"From 2 messages · 1 attachment",
    sum:["The Q3 cycle opens Monday and closes on the 29th.",
         "Six of your reports need a written review; two new starters use the short form.",
         "Calibration sessions are being booked this week and you choose the slot."],
    draft:"Noted. All six will be written up before the 29th. Could you send the short form for the two new starters? I will take the Wednesday calibration slot if it is still free." },

  { from:"Stripe", pri:"high", act:true, cat:"finance", catName:"Finance", imp:false, unread:true,
    subj:"Invoice INV-20418 failed, card declined",
    snip:"We will retry in three days. Update the card to avoid a lapse.",
    short:"INV-20418 failed, card declined", time:"09:02", meta:"From 1 message",
    sum:["The card on file was declined for INV-20418, $1,480.",
         "Stripe retries on the 26th, and the account lapses if that fails too.",
         "That card expired last month. Finance already holds the replacement."],
    draft:"The card on file expired last month. Finance is adding the replacement today, well ahead of the retry on the 26th, so there is no need to suspend the account." },

  { from:"Nadia Kerr", pri:"medium", act:true, cat:"finance", catName:"Finance", imp:true, unread:true,
    subj:"Lumina's cost per result rose 34% last week",
    snip:"Happy to look at the campaign structure with you.",
    short:"Cost per result rose 34%", time:"05:08", meta:"From 3 messages",
    sum:["Cost per result rose 34% week on week, almost all of it on the retargeting set.",
         "Nadia has offered a working session to restructure the campaign.",
         "Three times are proposed this week and one needs picking."],
    draft:"Thanks Nadia. Thursday at 10am suits best. Could you bring the breakdown by ad set so we can see exactly where the retargeting spend went?" },

  { from:"Dr. R. Lindqvist", count:2, pri:"medium", act:true, cat:"academic", catName:"Academic", imp:true, unread:true,
    subj:"Revisions requested: Journal of Applied Systems submission",
    snip:"Reviewer 2 wants the methodology section expanded.",
    short:"Journal of Applied Systems revisions", time:"14 Sept", meta:"From 4 messages · 1 attachment",
    sum:["Both reviewers recommend acceptance with minor revisions.",
         "Reviewer 2 wants the methodology expanded and the sample size justified.",
         "The revised manuscript is due back within 21 days."],
    draft:"Thank you both for the careful reads. I will expand the methodology with the sampling rationale, address the sample size directly, and return the revised manuscript well inside the 21 days." },

  { from:"Priya, Tom", count:3, pri:"medium", act:false, cat:"meeting", catName:"Meeting", imp:true, unread:true,
    subj:"Halcyon MVP discussion, meeting report",
    snip:"Notes, decisions and the three follow-ups.",
    short:"Halcyon MVP meeting report", time:"14 Sept", meta:"From 5 messages · 1 attachment",
    sum:["MVP scope was cut to onboarding, the dashboard and export.",
         "Launch moved to 20 October to protect the QA window.",
         "Three follow-ups were assigned, none of them to you."],
    draft:null },

  { from:"Northwind Studio", pri:"medium", act:true, cat:"meeting", catName:"Meeting", imp:true, unread:true,
    subj:"Invitation: Discovery call, Tue 15 Sept 3:30pm",
    snip:"Accept, decline or propose a new time.",
    short:"Discovery call, Tue 15 Sept", time:"14 Sept", meta:"From 1 message · calendar invitation",
    sum:["A one hour discovery call is proposed for Tuesday at 3:30pm.",
         "It clashes with your standing design review in Calendar.",
         "Three alternative times are offered in the invitation."],
    draft:"Tuesday at 3:30 clashes with our design review. Could we take the 4:30 slot instead? Happy to hold the Wednesday option as a backup if that is easier at your end." },

  { from:"Meta for Business", count:2, pri:"medium", act:true, cat:"other", catName:"Other", imp:false, unread:true,
    subj:"Verification requirements for advertisers change in October",
    snip:"Business verification is required from 1 October.",
    short:"Advertiser verification in October", time:"14 Sept", meta:"From 2 messages",
    sum:["Business verification becomes mandatory on 1 October.",
         "Unverified accounts lose the ability to launch new campaigns.",
         "Verification needs an ABN certificate and a recent bank statement."],
    draft:"Could you start the verification this week? We will need the ABN certificate and a recent bank statement uploaded before 1 October so no campaigns get blocked." },

  { from:"Martin Oyelaran", pri:"medium", act:true, cat:"other", catName:"Other", imp:true, unread:true,
    subj:"Kestrel: next steps after Tuesday's call",
    snip:"Great to meet you. Here is what we discussed.",
    short:"Kestrel next steps", time:"14 Sept", meta:"From 2 messages · 1 attachment",
    sum:["Martin has sent a pilot scope covering two sites over eight weeks.",
         "Pricing is held until the end of the month.",
         "He has asked whether procurement should be brought in now."],
    draft:"Good to meet you too. The two-site, eight-week pilot reads well. I will bring procurement in this week so we are not held up at signature. The pricing hold to month end is appreciated." },

  { from:"Apple", pri:"low", act:false, cat:"other", catName:"Other", imp:false, unread:false,
    subj:"Pre-order the new iPhone 18 Pro",
    snip:"Pro-level performance. Next-level battery.",
    short:"Pre-order iPhone 18 Pro", time:"13 Sept", meta:"From 1 message",
    sum:["Pre-orders open Friday with delivery from the 26th.",
         "Trade-in values are held for fourteen days.",
         "Nothing here needs a decision from you."],
    draft:null },

  { from:"Vantage AI", pri:"low", act:false, cat:"other", catName:"Other", imp:false, unread:false,
    subj:"Your monthly summary for your workspaces",
    snip:"Usage, seats and the three workspaces you own.",
    short:"Monthly workspace summary", time:"12 Sept", meta:"From 1 message",
    sum:["Usage sits at 62% of the plan with nine days left in the cycle.",
         "Two of eleven seats are unassigned.",
         "Nothing is required before the renewal on 1 October."],
    draft:null },

  { from:"Hubstaff", pri:"low", act:true, cat:"hr", catName:"People", imp:false, unread:false,
    subj:"T. Alvarez is 1 hour from weekly overtime",
    snip:"Review the timesheet before Friday's payroll run.",
    short:"Timesheet approaching overtime", time:"12 Sept", meta:"From 1 message",
    sum:["Alvarez is one hour from weekly overtime on the current timesheet.",
         "Approving as it stands adds roughly $96 to Friday's payroll run.",
         "You can approve, trim the hours, or let it roll into next week."],
    draft:"Approved. Let the hour stand this week; it came out of the Wednesday site visit. Please flag it to me again if it happens two weeks running." },

  { from:"Google Ads", pri:"low", act:false, cat:"other", catName:"Other", imp:false, unread:false,
    subj:"Updates to developer policies take effect 4 November",
    snip:"Policy changes affect API access and reporting.",
    short:"Developer policy updates", time:"11 Sept", meta:"From 1 message",
    sum:["Developer policy changes take effect on 4 November.",
         "API reporting scopes are narrowing and two of ours are affected.",
         "Nothing is required before November."],
    draft:null }
];

const PRI_LABEL = { high:"High", medium:"Medium", low:"Low" };
const PRI_ORDER = ["high", "medium", "low"];
const root = document.documentElement;
const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;

/* Hide the marks up front so they can land, but only when we intend to
   animate. The fallback below removes this again if Motion never loads. */
if (!reduced) root.classList.add("anim");

const icon = (id, w, cls) =>
  '<svg class="' + (cls || "") + '" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="'
  + (w || 1.5) + '" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><use href="#' + id + '"/></svg>';

/* The hero keeps its own copy of the marks, so re-triaging a row there
   does not silently rewrite the other demos further down the page. */
const HERO = THREADS.map((t) => Object.assign({}, t));

/* One mark renderer for all three glyphs. Each carries its own plain
   English tooltip, because a glyph you are meant to learn in passing
   has to be able to explain itself. */
function markHTML(t, kind, mode){
  let cls, tip, aria, body;
  if (kind === "pri") {
    cls = "mk-pri pri-" + t.pri;
    tip = PRI_LABEL[t.pri] + " priority";
    aria = "Priority: " + PRI_LABEL[t.pri] + (mode === "live" ? ". Activate to change it." : "");
    body = icon("tl-" + t.pri, 1.85);
  } else if (kind === "act") {
    cls = "mk-act";
    tip = t.act ? "Waiting on your reply" : "Nothing owed";
    aria = t.act ? "Waiting on you" + (mode === "live" ? ". Activate to clear." : "")
                 : "Nothing owed" + (mode === "live" ? ". Activate to flag a reply." : "");
    body = t.act ? icon("tl-action") + '<span class="slot-label">Reply</span>'
                 : '<span class="hair"></span><span class="slot-label">None</span>';
  } else {
    cls = "mk-cat";
    tip = t.catName;
    aria = "Category: " + t.catName;
    body = icon("tl-" + t.cat) + '<span class="slot-label">' + t.catName + "</span>";
  }
  const btn = mode === "live" || mode === "explore";
  const tag = btn ? "button" : "span";
  const extra = btn
    ? ' type="button" tabindex="' + (mode === "live" ? "-1" : "0") + '"'
    : ' role="img"';
  return "<" + tag + ' class="mk ' + cls + '" data-mk="' + kind + '" data-tip="' + tip + '"'
       + extra + ' aria-label="' + aria + '">' + body + "</" + tag + ">";
}

function rowHTML(t, i, opts){
  opts = opts || {};
  const mode = opts.mode || "static";
  const rowBtn = !!opts.row;
  const tabi = opts.roving ? (i === 0 ? "0" : "-1") : "0";
  const rowAttrs = rowBtn
    ? ' role="button" tabindex="' + tabi + '" aria-label="' + t.from + ", " + t.subj + '"'
    : "";
  return '<div class="grow" data-unread="' + t.unread + '" data-i="' + i + '"' + rowAttrs + ">"
    + '<span class="gcb" aria-hidden="true"></span>'
    + icon("tl-star", 1.4, "gstar")
    + '<svg class="gimp" data-on="' + !!t.imp + '" viewBox="0 0 16 16" fill="' + (t.imp ? "currentColor" : "none")
      + '" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round" aria-hidden="true"><use href="#tl-imp"/></svg>'
    + '<span class="gfrom">' + t.from + (t.count ? '<i class="gcount">' + t.count + "</i>" : "") + "</span>"
    + '<span class="slot slot-left stamp">' + markHTML(t, "pri", mode) + markHTML(t, "act", mode) + "</span>"
    + '<span class="gsubj"><b>' + t.subj + '</b><span class="snip"> - ' + t.snip + "</span></span>"
    + '<span class="slot slot-right stamp">' + markHTML(t, "cat", mode) + "</span>"
    + '<span class="gtime">' + t.time + "</span>"
    + "</div>";
}

const heroRows = document.getElementById("heroRows");
heroRows.innerHTML = HERO.map((t, i) => rowHTML(t, i, { mode:"live", row:true, roving:true })).join("");
document.getElementById("specRow").innerHTML = rowHTML(THREADS[0], 0, { mode:"explore" });
document.getElementById("panelRows").innerHTML = THREADS.slice(0, 6).map((t, i) => rowHTML(t, i, { row:true })).join("");
document.getElementById("themeInbox").innerHTML = THREADS.slice(0, 4).map((t, i) => rowHTML(t, i)).join("");

/* ---- theme: Threadly follows Gmail's, the page follows the reader's ---- */
let storedTheme = null;
try { storedTheme = localStorage.getItem("threadly-theme"); } catch (e) {}
if (storedTheme === "light" || storedTheme === "dark") root.setAttribute("data-theme", storedTheme);

function toggleTheme(){
  const now = root.getAttribute("data-theme")
    || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  const next = now === "dark" ? "light" : "dark";
  root.setAttribute("data-theme", next);
  try { localStorage.setItem("threadly-theme", next); } catch (e) {}
}
document.getElementById("themeBtn").addEventListener("click", toggleTheme);
document.getElementById("themeBtn2").addEventListener("click", toggleTheme);

/* ---- sticky header hairline ---- */
const sentinel = document.createElement("div");
sentinel.style.cssText = "position:absolute;top:0;left:0;width:1px;height:1px;pointer-events:none";
document.body.prepend(sentinel);
new IntersectionObserver(([entry]) => {
  document.getElementById("siteHead").dataset.stuck = String(!entry.isIntersecting);
}).observe(sentinel);

/* ---- text-label switch: the 62 to 120 and 56 to 110 widths from the spec ---- */
const specSheet = document.getElementById("specSheet");
const anatCards = document.getElementById("anatCards");
document.getElementById("labelToggle").addEventListener("change", (e) => {
  specSheet.classList.toggle("labels-on", e.target.checked);
});

/* Hovering a mark lights up the card that explains it, and the other
   way round, so the glyph and its meaning are never far apart. */
function hot(kind){
  specSheet.querySelectorAll(".mk").forEach((m) => {
    m.dataset.hot = String(!!kind && m.dataset.mk === kind);
  });
  anatCards.querySelectorAll(".anat-card").forEach((c) => {
    c.dataset.on = String(!!kind && c.dataset.mark === kind);
  });
}
["mouseover", "focusin", "click"].forEach((ev) => {
  specSheet.addEventListener(ev, (e) => {
    const m = e.target.closest(".mk");
    if (m) hot(m.dataset.mk);
  });
  anatCards.addEventListener(ev, (e) => {
    const c = e.target.closest(".anat-card");
    if (c) hot(c.dataset.mark);
  });
});
specSheet.addEventListener("mouseleave", () => hot(null));
anatCards.addEventListener("mouseleave", () => hot(null));
specSheet.addEventListener("focusout", () => hot(null));

/* ------------------------------------------------------------------
   Animation engine. If it never arrives the page is simply static.
------------------------------------------------------------------- */
let M = null;
try {
  if (!reduced) M = await import("motion");
} catch (e) { M = null; }
if (!M) root.classList.remove("anim");

const wait = (ms) => new Promise((r) => setTimeout(r, ms));

/* ------------------------------------------------------------------
   One panel, built twice: once beside the hero inbox and once in the
   section that explains it. Same markup, same behaviour, own state.
------------------------------------------------------------------- */
function panelMarkup(name){
  return ''
  + '<div class="tp-head">' + icon("tl-mark", 1.5, "mark") + "<b>Threadly</b>"
  +   '<span class="live-dot" aria-hidden="true"></span>'
  +   '<span class="spacer">'
  +     '<button class="tp-icon" data-p="collapse" type="button" aria-label="Collapse the panel">' + icon("tl-sidebar") + "</button>"
  +   "</span>"
  + "</div>"
  + '<div class="tp-body">'
  +   '<p class="tp-eyebrow">Thread context</p>'
  +   '<div class="tp-context">' + icon("tl-legal", 1.5, "tp-cat")
  +     '<span class="subj"></span>'
  +     '<button class="x" data-p="close" type="button" aria-label="Clear the thread">' + icon("tl-x", 1.7) + "</button>"
  +   "</div>"
  +   '<div class="tp-sumhead">' + icon("tl-mark", 1.5, "tp-modeicon") + '<span class="tp-eyebrow tp-modelabel">Summary</span></div>'
  +   '<div class="tp-out" aria-live="polite"></div>'
  +   '<div class="tp-foot"><span class="tp-meta"></span></div>'
  + "</div>"
  + '<div class="tp-actions">'
  +   '<button class="tp-act" type="button" data-p="mode" data-mode="sum" aria-pressed="true">' + icon("tl-summarise") + "Summarise</button>"
  +   '<button class="tp-act" type="button" data-p="mode" data-mode="draft" aria-pressed="false">' + icon("tl-draft") + "Draft reply</button>"
  + "</div>"
  + '<div class="tp-composer">'
  +   '<input type="text" id="ask-' + name + '" placeholder="Ask about this thread…" aria-label="Ask about this thread">'
  +   '<div class="tp-comfoot"><span class="scope"></span>'
  +     '<span class="mic" aria-hidden="true">' + icon("tl-voice") + "</span>"
  +     '<span class="send" aria-hidden="true">' + icon("tl-send", 1.7) + "</span></div>"
  + "</div>"
  + '<div class="tp-user"><span class="tp-avatar">SR</span><span>Sam Rivera</span></div>'
  + '<div class="tp-rail">'
  +   '<button class="tp-icon" data-p="collapse" type="button" aria-label="Expand the panel">' + icon("tl-sidebar") + "</button>"
  +   '<span class="tp-icon" aria-hidden="true">' + icon("tl-summarise") + "</span>"
  +   '<span class="tp-icon" aria-hidden="true">' + icon("tl-draft") + "</span>"
  +   '<span class="rail-sep"></span><span class="tp-avatar">SR</span>'
  + "</div>";
}

class Panel {
  constructor(el, opts){
    this.el = el;
    this.opts = opts || {};
    this.name = (opts && opts.name) || el.id || "panel";
    el.innerHTML = panelMarkup(this.name);
    this.q = (sel) => el.querySelector(sel);
    this.i = 0; this.mode = "sum"; this.tok = 0;
    el.addEventListener("click", (e) => {
      const b = e.target.closest("[data-p]");
      if (!b) return;
      if (b.dataset.p === "mode") { this.mode = b.dataset.mode; this.render(); }
      else if (b.dataset.p === "collapse") {
        el.dataset.collapsed = el.dataset.collapsed === "true" ? "false" : "true";
      } else if (b.dataset.p === "close" && this.opts.onClose) this.opts.onClose();
    });
    this.setThread(0);
  }
  setThread(i){
    this.i = i;
    const t = THREADS[i];
    this.q(".subj").textContent = t.short;
    this.q(".tp-meta").textContent = t.meta;
    this.q(".scope").textContent = "This thread · " + t.catName;
    this.q(".tp-cat").innerHTML = '<use href="#tl-' + t.cat + '"/>';
  }
  async render(){
    const t = THREADS[this.i];
    const mine = ++this.tok;
    const out = this.q(".tp-out");
    this.q(".tp-modelabel").textContent = this.mode === "sum" ? "Summary" : "Draft reply";
    this.q(".tp-modeicon").innerHTML = '<use href="#tl-' + (this.mode === "sum" ? "mark" : "draft") + '"/>';
    this.el.querySelectorAll(".tp-act").forEach((b) => {
      b.setAttribute("aria-pressed", String(b.dataset.mode === this.mode));
    });

    if (M) {
      out.innerHTML = '<div class="thinking"><i></i><i></i><i></i></div>';
      M.animate(out.querySelectorAll(".thinking i"), { opacity: [0.25, 1, 0.25] },
        { duration: 0.9, repeat: Infinity, delay: M.stagger(0.13) });
      await wait(460);
      if (mine !== this.tok) return;
    }

    if (this.mode === "sum") {
      out.innerHTML = '<ul class="tp-bullets">' + t.sum.map((x) => "<li>" + x + "</li>").join("") + "</ul>";
    } else if (t.draft) {
      out.innerHTML = '<p class="tp-draft">' + t.draft + "</p>";
    } else {
      out.innerHTML = '<p class="tp-draft none">Nothing in this thread is waiting on a reply, so Threadly did not draft one.</p>';
    }
    if (M) {
      M.animate(out.querySelectorAll(".tp-bullets li, .tp-draft"),
        { opacity: [0, 1], transform: ["translateY(6px)", "translateY(0px)"] },
        { duration: 0.34, delay: M.stagger(0.07), ease: [0.2, 0.7, 0.3, 1] });
    }
  }
}

/* ------------------------------------------------------------------
   The hero inbox is the real thing: open a thread, change a mark,
   drive the whole list from the keyboard.
------------------------------------------------------------------- */
const heroFrame = document.getElementById("heroFrame");
const heroNote  = document.getElementById("heroNote");
const REST_NOTE = heroNote.innerHTML;
const heroPanel = new Panel(document.getElementById("heroPanel"), { onClose: closeHero });
const mainPanel = new Panel(document.getElementById("mainPanel"));

let sayTimer = 0;
function say(msg){
  heroNote.textContent = msg;
  heroNote.dataset.said = "true";
  clearTimeout(sayTimer);
  sayTimer = setTimeout(() => {
    heroNote.innerHTML = REST_NOTE;
    heroNote.dataset.said = "false";
  }, 4600);
}

function markRow(i){
  heroRows.querySelectorAll(".grow").forEach((el) => {
    el.setAttribute("aria-current", String(Number(el.dataset.i) === i));
  });
}
function openHero(i){
  heroPanel.setThread(i);
  heroPanel.render();
  heroFrame.dataset.open = "true";
  markRow(i);
}
function closeHero(){
  heroFrame.dataset.open = "false";
  heroRows.querySelectorAll(".grow").forEach((el) => el.setAttribute("aria-current", "false"));
}

function repaintRow(i){
  const t = HERO[i];
  const slot = heroRows.querySelector('.grow[data-i="' + i + '"] .slot-left');
  const wasPri = slot.querySelector(".mk-pri").dataset.edited === "true";
  const wasAct = slot.querySelector(".mk-act").dataset.edited === "true";
  slot.innerHTML = markHTML(t, "pri", "live") + markHTML(t, "act", "live");
  const pri = slot.querySelector(".mk-pri");
  const act = slot.querySelector(".mk-act");
  if (wasPri) pri.dataset.edited = "true";
  if (wasAct) act.dataset.edited = "true";
  return { pri, act };
}
function bump(el){
  if (M) M.animate(el, { transform: ["scale(0.5)", "scale(1)"] },
    { type: "spring", visualDuration: 0.34, bounce: 0.45 });
}
function cyclePri(i){
  const t = HERO[i];
  t.pri = PRI_ORDER[(PRI_ORDER.indexOf(t.pri) + 1) % 3];
  const n = repaintRow(i);
  n.pri.dataset.edited = "true";
  bump(n.pri);
  say("Marked " + PRI_LABEL[t.pri].toLowerCase() + ". This preview changes the mark for " + t.from + ".");
}
function toggleAct(i){
  const t = HERO[i];
  t.act = !t.act;
  const n = repaintRow(i);
  n.act.dataset.edited = "true";
  bump(n.act);
  say(t.act ? "Flagged as waiting on you." : "Cleared. Nothing is owed on this one.");
}

function focusRow(i){
  const rows = heroRows.querySelectorAll(".grow");
  const n = Math.max(0, Math.min(rows.length - 1, i));
  rows.forEach((el, k) => el.setAttribute("tabindex", k === n ? "0" : "-1"));
  rows[n].focus();
}

heroRows.addEventListener("click", (e) => {
  const row = e.target.closest(".grow");
  if (!row) return;
  const i = Number(row.dataset.i);
  const mk = e.target.closest(".mk");
  if (mk && mk.dataset.mk === "pri")   { cyclePri(i); return; }
  if (mk && mk.dataset.mk === "act") { toggleAct(i); return; }
  focusRow(i);
  openHero(i);
});

heroRows.addEventListener("keydown", (e) => {
  const row = e.target.closest(".grow");
  if (!row) return;
  const i = Number(row.dataset.i);
  const k = e.key;
  const mark = e.target.closest(".mk");
  if (mark && (k === "Enter" || k === " ")) {
    e.preventDefault();
    if (mark.dataset.mk === "pri") cyclePri(i);
    else if (mark.dataset.mk === "act") toggleAct(i);
    else openHero(i);
    return;
  }
  if (k === "j" || k === "ArrowDown")      { e.preventDefault(); focusRow(i + 1); }
  else if (k === "k" || k === "ArrowUp")   { e.preventDefault(); focusRow(i - 1); }
  else if (k === "Enter" || k === " ")     { e.preventDefault(); openHero(i); }
  else if (k === "p" || k === "P")         { e.preventDefault(); cyclePri(i); }
  else if (k === "r" || k === "R")         { e.preventDefault(); toggleAct(i); }
});
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeHero(); });

document.getElementById("ctaPanel").addEventListener("click", () => {
  focusRow(0);
  openHero(0);
});

/* ---- the section further down drives the second panel ---- */
const panelRows = document.getElementById("panelRows");
panelRows.addEventListener("click", (e) => {
  const row = e.target.closest(".grow");
  if (!row) return;
  const i = Number(row.dataset.i);
  panelRows.querySelectorAll(".grow").forEach((el) => {
    el.setAttribute("aria-current", String(Number(el.dataset.i) === i));
  });
  mainPanel.setThread(i);
  mainPanel.render();
});
panelRows.addEventListener("keydown", (e) => {
  if (e.key !== "Enter" && e.key !== " ") return;
  const row = e.target.closest(".grow");
  if (!row) return;
  e.preventDefault();
  row.click();
});
panelRows.querySelector('.grow[data-i="0"]').setAttribute("aria-current", "true");
mainPanel.render();

/* ------------------------------------------------------------------
   One orchestrated load sequence: the marks land, top to bottom.
   This is literally what the extension does to an inbox.
------------------------------------------------------------------- */
if (M) {
  M.animate(document.querySelectorAll("#heroRows .stamp"),
    { opacity: [0, 1], transform: ["scale(0.55)", "scale(1)"] },
    { type: "spring", visualDuration: 0.42, bounce: 0.34, delay: M.stagger(0.045, { startDelay: 0.25 }) });

  const readEl = document.getElementById("ctRead");
  const replyEl = document.getElementById("ctReply");
  readEl.textContent = "0";
  replyEl.textContent = "0";
  const countTo = (el, to) => M.animate(0, to, {
    duration: 1.1, ease: "easeOut", delay: 0.3,
    onUpdate: (v) => { el.textContent = Math.round(v); }
  });
  countTo(readEl, 71);
  countTo(replyEl, 12);
}

/* ------------------------------------------------------------------
   Your week. Minutes from midnight; the grid runs 09:00 to 18:00 in
   half hours, so row = 2 + (minutes - 540) / 30.
------------------------------------------------------------------- */
const DAYS = ["Mon 14", "Tue 15", "Wed 16", "Thu 17", "Fri 18"];
const BUSY = [
  [0, 570, 630, "Standup"],        [0, 840, 900, "1:1 with Priya"],
  [1, 660, 720, "Sprint review"],  [1, 900, 960, "Design review"],
  [2, 540, 570, "Standup"],        [2, 780, 870, "Client workshop"],
  [3, 600, 660, "Finance sync"],
  [4, 570, 630, "Standup"],        [4, 930, 1020, "Retro"]
];
const PROPOSED = [1, 930, 990];   // Tue 15:30 to 16:30, straight over the design review
const SUGGEST = [
  { d:1, t:990, label:"Tue 4:30pm",
    reply:"Tuesday at 3:30 clashes with our design review. Could we take the 4:30 slot instead? I am free straight after." },
  { d:2, t:600, label:"Wed 10:00am",
    reply:"Tuesday at 3:30 clashes with our design review. Wednesday at 10 is clear at my end if that suits you." },
  { d:3, t:840, label:"Thu 2:00pm",
    reply:"Tuesday at 3:30 clashes with our design review. Thursday at 2 is open on my side and gives us the full hour." }
];
const wkRow = (m) => 2 + (m - 540) / 30;
const weekEl = document.getElementById("week");
const slotRow = document.getElementById("slotRow");
const weekReply = document.getElementById("weekReply");
const weekState = document.getElementById("weekState");

function drawWeek(pick){
  let h = "";
  for (let i = 0; i < DAYS.length; i++)
    h += '<span class="wk-day" style="grid-column:' + (i + 2) + '">' + DAYS[i] + "</span>";
  for (let hr = 9; hr < 18; hr++)
    h += '<span class="wk-hr" style="grid-row:' + (2 + (hr - 9) * 2) + '">'
       + (hr > 12 ? hr - 12 : hr) + (hr < 12 ? "am" : "pm") + "</span>";
  for (let i = 0; i < DAYS.length; i++)
    h += '<span class="wk-col' + (i === 4 ? " last" : "") + '" style="grid-column:' + (i + 2) + ';grid-row:2/20"></span>';
  for (let i = 0; i < BUSY.length; i++){
    const b = BUSY[i];
    h += '<span class="wk-ev" style="grid-column:' + (b[0] + 2) + ';grid-row:' + wkRow(b[1]) + "/" + wkRow(b[2]) + '">' + b[3] + "</span>";
  }
  if (pick === null){
    h += '<span class="wk-ev wk-clash" style="grid-column:' + (PROPOSED[0] + 2)
       + ';grid-row:' + wkRow(PROPOSED[1]) + "/" + wkRow(PROPOSED[2]) + '">Clash</span>';
  } else {
    const g = SUGGEST[pick];
    h += '<span class="wk-ev wk-pick" style="grid-column:' + (g.d + 2)
       + ';grid-row:' + wkRow(g.t) + "/" + wkRow(g.t + 60) + '">Discovery call</span>';
  }
  weekEl.innerHTML = h;
}

slotRow.innerHTML = SUGGEST.map((g, i) =>
  '<button class="slotbtn" type="button" data-i="' + i + '" aria-pressed="false">' + g.label + "</button>").join("");
drawWeek(null);

slotRow.addEventListener("click", (e) => {
  const b = e.target.closest(".slotbtn");
  if (!b) return;
  const i = Number(b.dataset.i);
  slotRow.querySelectorAll(".slotbtn").forEach((x) => {
    x.setAttribute("aria-pressed", String(Number(x.dataset.i) === i));
  });
  drawWeek(i);
  weekReply.textContent = SUGGEST[i].reply;
  weekState.textContent = "Preview: " + SUGGEST[i].label;
  if (M) {
    M.animate(weekReply, { opacity: [0, 1], transform: ["translateY(5px)", "translateY(0px)"] },
      { duration: 0.3, ease: [0.2, 0.7, 0.3, 1] });
    M.animate(weekEl.querySelector(".wk-pick"),
      { opacity: [0, 1], transform: ["scale(0.9)", "scale(1)"] },
      { type: "spring", visualDuration: 0.34, bounce: 0.3 });
  }
});

/* ------------------------------------------------------------------
   Voice
------------------------------------------------------------------- */
const BARS = [8,14,22,11,28,17,9,24,31,13,19,26,10,21,33,15,7,23,29,12,18,25,9,16,27,11,20,14];
const wave = document.getElementById("wave");
const micBtn = document.getElementById("micBtn");
const micLabel = document.getElementById("micLabel");
const voiceOut = document.getElementById("voiceOut");
wave.innerHTML = BARS.map((h, i) =>
  '<i style="--h:' + h + "px;animation-delay:" + (i * 0.055).toFixed(2) + 's"></i>').join("");

let listening = false, vTok = 0;
function stopListening(){
  listening = false;
  wave.classList.remove("on");
  micBtn.setAttribute("aria-pressed", "false");
  micLabel.textContent = "Play the example";
}

micBtn.addEventListener("click", async () => {
  const mine = ++vTok;
  if (listening) { stopListening(); return; }

  listening = true;
  wave.classList.add("on");
  micBtn.setAttribute("aria-pressed", "true");
  micLabel.textContent = "Playing example";
  voiceOut.innerHTML = '<p class="v-hint">Example on the Hartley &amp; Vance thread. No microphone is recording.</p>';

  await wait(1500);
  if (mine !== vTok) return;

  stopListening();
  voiceOut.innerHTML =
      '<p class="v-q">“What did they say about the indemnity cap?”</p>'
    + '<p class="v-a">Counsel want it at six months of fees. Your last position was twelve. Finance has only signed off on the payment terms, not the cap.</p>'
    + '<p class="v-hint">Say “reply with that” and Threadly puts it in a draft for you to read before it goes anywhere.</p>';

  if (M) {
    M.animate(voiceOut.querySelectorAll("p"),
      { opacity: [0, 1], transform: ["translateY(6px)", "translateY(0px)"] },
      { duration: 0.32, delay: M.stagger(0.09), ease: [0.2, 0.7, 0.3, 1] });
  }
});

