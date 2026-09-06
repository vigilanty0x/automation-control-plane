const state = { profile: "" };
const $ = id => document.getElementById(id);

async function api(path, options = {}) {
  const mutation = options.method && options.method !== "GET";
  const response = await fetch(path, {
    credentials: "same-origin",
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(mutation ? { "Idempotency-Key": crypto.randomUUID() } : {}),
      ...(options.headers || {})
    }
  });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error?.message || `HTTP ${response.status}`);
  return payload;
}

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, character => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  })[character]);
}

function metric(value, label) {
  return `<div class="metric"><strong>${esc(value)}</strong><span>${esc(label)}</span></div>`;
}

function renderList(id, items, renderer) {
  $(id).innerHTML = items.length
    ? items.map(renderer).join("")
    : '<span class="empty">Aucun élément.</span>';
}

function item(title, detail, badge = "") {
  return `<article class="item"><div class="item-row"><strong>${esc(title)}</strong>${
    badge ? `<span class="badge">${esc(badge)}</span>` : ""
  }</div><p>${esc(detail)}</p></article>`;
}

function questionCard(question) {
  const utility = question.expected_utility || {};
  const open = ["queued", "shown", "snoozed"].includes(question.status);
  const answers = open && question.status !== "snoozed"
    ? ["yes", "no", "unknown"].map(choice => `<button class="mini accent" data-question="${esc(question.id)}" data-op="answer" data-choice="${choice}">${choice}</button>`).join("")
    : "";
  const lifecycle = !open ? "" : question.status === "snoozed"
    ? `<button class="mini" data-question="${esc(question.id)}" data-op="resume">Reprendre</button>`
    : `<button class="mini" data-question="${esc(question.id)}" data-op="snooze">Reporter 24 h</button><button class="mini" data-question="${esc(question.id)}" data-op="dismiss">Rejeter</button>`;
  const details = { evidence_refs: question.evidence_refs, consequence_preview: question.consequence_preview };
  return `<article class="item"><div class="item-row"><strong>${esc(question.short_text || question.id)}</strong><span class="badge">${esc(question.status)}</span></div>
    <p><b>Pourquoi maintenant :</b> ${esc(question.explanation || "Preuve de branche disponible.")}</p>
    <p>Utilité ${esc(utility.score ?? "?")} · information ${esc(utility.information_gain ?? "?")} · coût ${esc(utility.interruption_cost ?? "?")}</p>
    <details><summary>Preuves et conséquences</summary><pre>${esc(JSON.stringify(details, null, 2))}</pre></details>
    <div class="actions">${answers}${lifecycle}</div></article>`;
}

function routineCard(routine) {
  const compile = routine.status === "confirmed"
    ? `<button class="mini accent" data-routine="${esc(routine.routine_id)}">Compiler en Skill IR</button>`
    : "";
  const details = { branches: routine.branches, induction_ids: routine.induction_ids, holdout_ids: routine.holdout_ids };
  return `<article class="item"><div class="item-row"><strong>${esc(routine.title || routine.intent)}</strong><span class="badge">${esc(routine.status)}</span></div>
    <p>${esc(routine.scores?.occurrences || 0)} occurrences · holdout ${esc(Math.round((routine.scores?.holdout_pass_rate || 0) * 100))}%</p>
    <details><summary>Règle et provenance</summary><pre>${esc(JSON.stringify(details, null, 2))}</pre></details><div class="actions">${compile}</div></article>`;
}

function skillCard(skill) {
  return `<article class="item"><div class="item-row"><strong>${esc(skill.skill_id)}</strong><span class="badge">${esc(skill.version)}</span></div>
    <p>${esc(skill.steps.length)} étapes · réseau refusé · aucune exécution</p>
    <details><summary>Skill IR complet</summary><pre>${esc(JSON.stringify(skill, null, 2))}</pre></details>
    <div class="actions"><button class="mini accent" data-skill="${esc(skill.skill_id)}" data-version="${esc(skill.version)}">Prévisualiser</button></div></article>`;
}

async function profiles(selectLatest = false) {
  const data = await api("/api/v1/profiles");
  const active = data.profiles.filter(profile => profile.status === "active");
  $("profile").innerHTML = active.length
    ? active.map(profile => `<option value="${esc(profile.profile_id)}">${esc(profile.name)} · ${esc(profile.profile_id.slice(-8))}</option>`).join("")
    : '<option value="">Aucun profil</option>';
  if (selectLatest && active.length) $("profile").value = active.at(-1).profile_id;
  state.profile = $("profile").value;
  await refresh();
}

async function refresh() {
  if (!state.profile) {
    ["chains", "events", "routines", "questions", "memories", "skills", "imports", "audit"].forEach(id => renderList(id, [], () => ""));
    $("metrics").innerHTML = [0, 0, 0, 0].map((value, index) => metric(value, ["Épisodes", "Routines", "Questions", "Skills"][index])).join("");
    return;
  }
  const base = `/api/v1/profiles/${state.profile}`;
  $("notice").textContent = "Synchronisation locale…";
  try {
    const [sessions, events, episodes, routines, questions, memories, skills, imports, audit] = await Promise.all([
      api(`${base}/sessions`), api(`${base}/timeline?limit=100`), api(`${base}/episodes`), api(`${base}/routines`),
      api(`${base}/questions`), api(`${base}/memories`), api(`${base}/skills`), api(`${base}/imports`), api(`${base}/audit`)
    ]);
    const chains = await Promise.all(sessions.sessions.map(session => api(`${base}/sessions/${session.session_id}/verify`)));
    $("metrics").innerHTML = metric(episodes.episodes.length, "Épisodes") + metric(routines.routines.length, "Routines") + metric(questions.questions.length, "Questions") + metric(skills.skills.length, "Skills actifs");
    renderList("chains", chains, chain => item(chain.session_id, `${chain.events} événements · tête ${chain.head.slice(0, 14)}…`, chain.valid && chain.sealed ? "scellée" : "à vérifier"));
    renderList("events", events.events, event => item(event.action?.kind || "événement", `${event.application?.id || "app"} · ${event.timestamp}`, event.privacy?.classification || "D1"));
    renderList("routines", routines.routines, routineCard);
    renderList("questions", questions.questions, questionCard);
    renderList("memories", memories.memories, memory => item(`${memory.subject} ${memory.predicate}`, JSON.stringify(memory.object), memory.status));
    renderList("skills", skills.skills, skillCard);
    renderList("imports", imports.imports, pack => item(pack.skill_id || pack.import_id, pack.source_digest, pack.trust_state));
    renderList("audit", audit.audit.slice(-30).reverse(), entry => item(`${entry.component} · ${entry.action}`, entry.reason_code, entry.occurred_at.slice(0, 19)));
    $("notice").classList.remove("error");
    $("notice").textContent = "À jour";
  } catch (error) {
    $("notice").textContent = `Erreur: ${error.message}`;
    $("notice").classList.add("error");
  }
}

document.querySelectorAll(".tab").forEach(button => button.addEventListener("click", () => {
  document.querySelectorAll(".tab,.panel").forEach(node => node.classList.remove("active"));
  button.classList.add("active");
  $(button.dataset.panel).classList.add("active");
}));

$("questions").addEventListener("click", async event => {
  const button = event.target.closest("button[data-question]");
  if (!button) return;
  const base = `/api/v1/profiles/${state.profile}/questions/${button.dataset.question}`;
  const operation = button.dataset.op;
  let body = {};
  if (operation === "answer") body = { choice: button.dataset.choice, synthetic: true };
  if (operation === "snooze") body = { until: new Date(Date.now() + 86400000).toISOString() };
  try { await api(`${base}/${operation}`, { method: "POST", body: JSON.stringify(body) }); await refresh(); }
  catch (error) { $("notice").textContent = `Erreur: ${error.message}`; }
});

$("routines").addEventListener("click", async event => {
  const button = event.target.closest("button[data-routine]");
  if (!button) return;
  try { await api(`/api/v1/profiles/${state.profile}/routines/${button.dataset.routine}/compile`, { method: "POST", body: "{}" }); await refresh(); }
  catch (error) { $("notice").textContent = `Erreur: ${error.message}`; }
});

$("skills").addEventListener("click", async event => {
  const button = event.target.closest("button[data-skill]");
  if (!button) return;
  try {
    const preview = await api(`/api/v1/profiles/${state.profile}/skills/${button.dataset.skill}/${button.dataset.version}/preview`, { method: "POST", body: JSON.stringify({ inputs: { source_dataset: "fixture://D6" } }) });
    $("preview").textContent = JSON.stringify(preview, null, 2);
  } catch (error) { $("preview").textContent = `Erreur: ${error.message}`; }
});

$("profile").addEventListener("change", () => { state.profile = $("profile").value; refresh(); });
$("refresh").addEventListener("click", refresh);
$("run-demo").addEventListener("click", async () => {
  const button = $("run-demo");
  button.disabled = true;
  button.textContent = "Construction des preuves…";
  try {
    await api("/api/v1/demo/observe", { method: "POST", body: "{}" });
    await profiles(true);
    $("notice").textContent = "Preuves prêtes : une décision humaine est requise";
  } catch (error) { $("notice").textContent = `Erreur: ${error.message}`; }
  finally { button.disabled = false; button.textContent = "Préparer D1–D5 pour validation"; }
});

api("/api/v1/capabilities").then(data => $("capabilities").textContent = JSON.stringify(data, null, 2)).catch(error => $("capabilities").textContent = error.message);
profiles().catch(error => $("notice").textContent = `Erreur: ${error.message}`);
