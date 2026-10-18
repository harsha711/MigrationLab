(() => {
  const add = (parent, tag, text, className) => {
    const node = document.createElement(tag);
    node.textContent = text;
    if (className) node.className = className;
    parent.appendChild(node);
    return node;
  };

  const citationLink = (id) => {
    const link = document.createElement("a");
    link.textContent = id;
    link.href = id.includes("#") ? `#/obs/${encodeURIComponent(id)}` : `#/run/${encodeURIComponent(id)}`;
    return link;
  };

  document.addEventListener("click", (event) => {
    const button = event.target.closest("[data-chat-question]");
    if (!button) return;
    const question = document.getElementById("chat-question");
    if (!question) return;
    question.value = button.dataset.chatQuestion;
    question.focus();
    question.setSelectionRange(question.value.length, question.value.length);
    const composer = document.querySelector(".ask-composer");
    if (composer && composer.getBoundingClientRect().top < 0) composer.scrollIntoView({block: "start"});
  });

  document.addEventListener("keydown", (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key === "Enter" && event.target.id === "chat-question") {
      event.preventDefault();
      event.target.form?.requestSubmit();
    }
  });

  document.addEventListener("submit", async (event) => {
    if (event.target.id !== "chat-form") return;
    event.preventDefault();
    const form = event.target;
    const question = form.querySelector("#chat-question");
    const result = document.getElementById("chat-result");
    const text = question.value.trim();
    if (!text) return;
    const submit = form.querySelector("button[type=submit]");
    submit.disabled = true;
    submit.textContent = "Checking evidence…";
    result.replaceChildren();
    const loading = add(result, "div", "", "answer-loading");
    add(loading, "span", "", "answer-spinner");
    add(loading, "p", "Looking through recorded runs and observations…");
    try {
      const response = await fetch("/api/chat", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({question: text})});
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
      result.replaceChildren();
      const records = (data.facts?.record_ids?.run_ids?.length || 0) + (data.facts?.record_ids?.observation_ids?.length || 0);
      const answer = add(result, "article", "", "answer-card");
      const header = add(answer, "header", "", "answer-head");
      add(header, "span", "Answer", "eyebrow");
      add(header, "span", data.mode === "model" ? "Model phrasing" : "Facts only", `badge ${data.mode === "model" ? "pass" : "warn"}`);
      add(answer, "h2", "What the evidence says");
      add(answer, "p", data.answer, "answer-copy");
      add(answer, "small", data.mode === "model" ? `Phrased by ${data.model || "the configured model"} · grounded in ${records} records` : `Deterministic response · grounded in ${records} records`, "answer-meta");
      if (data.citations?.length) {
        const citations = add(answer, "div", "", "chat-citations");
        add(citations, "strong", "Open supporting evidence");
        const links = add(citations, "div", "", "citation-links");
        data.citations.forEach((id, index) => {
          links.appendChild(citationLink(id));
        });
      }
      const details = document.createElement("details");
      details.className = "raw-facts";
      add(details, "summary", "Inspect the structured facts used");
      add(details, "pre", JSON.stringify(data.facts, null, 2));
      answer.appendChild(details);
    } catch (error) {
      result.replaceChildren();
      const failure = add(result, "div", "", "answer-error");
      add(failure, "strong", "We couldn’t retrieve an answer.");
      add(failure, "p", error.message);
      add(failure, "small", "Your question is still in the box. Try again, or open Browse runs to inspect the evidence directly.");
    } finally {
      submit.disabled = false;
      submit.textContent = "Ask evidence →";
    }
  });
})();
