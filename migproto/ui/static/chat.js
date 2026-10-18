(() => {
  const form = document.getElementById("chat-form");
  const question = document.getElementById("chat-question");
  const result = document.getElementById("chat-result");

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

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const text = question.value.trim();
    if (!text) return;
    result.replaceChildren();
    add(result, "p", "Looking through recorded evidence…", "muted");
    try {
      const response = await fetch("/api/chat", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({question: text})});
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
      result.replaceChildren();
      const records = (data.facts?.record_ids?.run_ids?.length || 0) + (data.facts?.record_ids?.observation_ids?.length || 0);
      add(result, "span", data.mode === "model" ? "model" : "facts only", `badge ${data.mode === "model" ? "pass" : "warn"}`);
      add(result, "small", data.mode === "model" ? ` Answer from ${data.model || "model"}, grounded in ${records} records` : ` Deterministic answer, grounded in ${records} records`);
      add(result, "p", data.answer);
      if (data.citations?.length) {
        const citations = add(result, "div", "Citations: ", "chat-citations");
        data.citations.forEach((id, index) => {
          if (index) citations.appendChild(document.createTextNode(" · "));
          citations.appendChild(citationLink(id));
        });
      }
      const details = document.createElement("details");
      add(details, "summary", "Facts used");
      add(details, "pre", JSON.stringify(data.facts, null, 2));
      result.appendChild(details);
    } catch (error) {
      result.replaceChildren();
      add(result, "p", `Could not answer: ${error.message}`, "notice");
    }
  });
})();
