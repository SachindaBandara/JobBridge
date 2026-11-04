const form = document.querySelector("#scan-form");
const workspace = document.querySelector("#workspace");
const jobList = document.querySelector("#job-list");
const alerts = document.querySelector("#alerts");
const historyPanel = document.querySelector("#history-panel");
const historyList = document.querySelector("#history-list");

function node(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}

async function request(path, data) {
  const response = await fetch(path, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(data)
  });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || `Request failed (${response.status}).`);
  return payload;
}

function reportError(message) {
  const alert = node("div", "alert", message);
  alerts.append(alert);
}

function safeExternalUrl(value) {
  try {
    const url = new URL(value);
    return ["http:", "https:"].includes(url.protocol) ? url.href : "";
  } catch {
    return "";
  }
}

function makeAssetColumn(title, items, formatter) {
  const column = node("div", "asset-column");
  const heading = node("div", "asset-title");
  heading.append(node("span", "", title), node("span", "asset-count", `${items.length} found`));
  column.append(heading);
  const list = node("div", "asset-list");
  if (!items.length) list.append(node("span", "asset-item", "None found"));
  items.forEach((item, index) => list.append(formatter(item, index)));
  column.append(list);
  return column;
}

function makeLink(link, index) {
  const label = link.text || `Link ${index + 1}`;
  return node("div", "asset-item", `${label} — ${link.url}`);
}

function makeImage(url, index) {
  const row = node("div", "asset-item", `Image ${index + 1} — ${url}`);
  return row;
}

function appendFact(container, label, value) {
  if (!value) return;
  const fact = node("span");
  fact.append(node("span", "fact-label", `${label}:`), document.createTextNode(` ${value}`));
  container.append(fact);
}

function makeSeoMetadata(labels, description, saved = false, title = "") {
  const seo = node("div", "seo-meta");
  seo.append(
    node("strong", "", saved ? "Blogger post metadata" : "Metadata to be saved"),
    node("p", "", `${saved ? "Post title" : "Blogger title"}: ${title || "Unavailable"}`),
    node("p", "", `Labels${saved ? " saved" : ""}: ${(labels || []).join(", ") || "None"}`),
    node("p", "", `Search description (paste into Blogger Post settings): ${description || "Unavailable"}`)
  );
  const copyDescription = node("button", "seo-copy", "Copy search description");
  copyDescription.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(description || "");
      copyDescription.textContent = "Copied";
    } catch {
      reportError("Could not copy the search description. Select and copy the text above manually.");
    }
  });
  seo.append(copyDescription);
  return seo;
}

function renderJob(job) {
  const card = node("article", "job-card");
  const top = node("div", "job-card-top");
  const titleGroup = node("div");
  const sourceUrl = safeExternalUrl(job.source_url);
  titleGroup.append(node("h3", "job-title", job.title || "Job opportunity"));
  titleGroup.append(node("p", "job-company", job.company || (sourceUrl ? new URL(sourceUrl).hostname : "Employer not specified")));
  top.append(titleGroup, node("span", `status-tag${job.status === "already processed" ? " done" : ""}`, job.status));
  card.append(top);

  const facts = node("div", "job-facts");
  appendFact(facts, "Location", job.location);
  appendFact(facts, "Type", job.employment_type);
  appendFact(facts, "Salary", job.salary);
  appendFact(facts, "Posted", job.date_posted);
  appendFact(facts, "Apply by", job.valid_through);
  if (facts.childElementCount) card.append(facts);
  card.append(makeSeoMetadata(job.suggested_labels, job.search_description, false, job.blogger_title));

  if (job.description) {
    const description = node("div", "job-description");
    description.append(node("strong", "", "Role details"), document.createTextNode(job.description));
    card.append(description);
  }
  if (job.summary) card.append(node("div", "ai-summary", job.summary));

  const assetGrid = node("div", "job-assets");
  assetGrid.append(
    makeAssetColumn("SOURCE IMAGES", job.images || [], makeImage),
    makeAssetColumn("PAGE LINKS", job.links || [], makeLink)
  );
  card.append(assetGrid);

  const actions = node("div", "card-actions");
  if (sourceUrl) {
    const source = node("a", "", "Open original ↗");
    source.href = sourceUrl;
    source.target = "_blank";
    source.rel = "noopener noreferrer";
    actions.append(source);
  }
  const summaryButton = node("button", "", job.summary ? "Summary ready" : "Summarize with Gemini");
  summaryButton.disabled = Boolean(job.summary);
  summaryButton.addEventListener("click", async () => {
    summaryButton.disabled = true;
    summaryButton.textContent = "Summarizing…";
    try {
      const result = await request("/api/summarize", {job});
      job.summary = result.summary;
      const summary = node("div", "ai-summary", result.summary);
      card.insertBefore(summary, assetGrid);
      summaryButton.textContent = "Summary ready";
    } catch (error) {
      summaryButton.disabled = false;
      summaryButton.textContent = "Summarize with Gemini";
      reportError(`${job.title}: ${error.message}`);
    }
  });
  actions.append(summaryButton);

  const imageButton = node("button", "", "Generate image");
  imageButton.addEventListener("click", async () => {
    imageButton.disabled = true;
    imageButton.textContent = "Generating…";
    try {
      const result = await request("/api/generate-image", {job});
      const preview = document.createElement("img");
      preview.src = result.image_url;
      preview.alt = `AI-generated illustration for ${job.title}`;
      preview.className = "generated-preview";
      assetGrid.firstElementChild.append(preview);
      imageButton.textContent = "Image generated";
    } catch (error) {
      imageButton.disabled = false;
      imageButton.textContent = "Generate image";
      reportError(`${job.title}: ${error.message}`);
    }
  });
  actions.append(imageButton);

  if (job.status !== "already processed") {
    const draftButton = node("button", "draft-button", "Save Blogger draft");
    draftButton.addEventListener("click", async () => {
      draftButton.disabled = true;
      draftButton.textContent = "Saving…";
      try {
        const result = await request("/api/publish", {job});
        draftButton.textContent = "Draft saved";
        const status = card.querySelector(".status-tag");
        status.textContent = "draft";
        status.classList.add("done");
        job.status = "already processed";
        card.querySelector(".seo-meta").replaceWith(
          makeSeoMetadata(result.labels, result.search_description, true, result.title)
        );
        const draftUrl = safeExternalUrl(result.url);
        if (draftUrl) {
          const draftLink = node("a", "", "View draft ↗");
          draftLink.href = draftUrl;
          draftLink.target = "_blank";
          draftLink.rel = "noopener noreferrer";
          actions.append(draftLink);
        }
      } catch (error) {
        draftButton.disabled = false;
        draftButton.textContent = "Save Blogger draft";
        reportError(`${job.title}: ${error.message}`);
      }
    });
    actions.append(draftButton);
  }
  card.append(actions);
  return card;
}

function showJobs(jobs, caption) {
  workspace.hidden = false;
  jobList.replaceChildren();
  alerts.replaceChildren();
  document.querySelector("#job-count").textContent = String(jobs.length);
  document.querySelector("#results-caption").textContent = caption;
  if (!jobs.length) jobList.append(node("div", "empty-state", "No job listings found. Try a feed URL or a page with direct job links."));
  jobs.forEach(job => jobList.append(renderJob(job)));
  workspace.scrollIntoView({behavior: "smooth", block: "start"});
}

form.addEventListener("submit", async event => {
  event.preventDefault();
  const button = document.querySelector("#scan-button");
  button.disabled = true;
  button.querySelector("span").textContent = "Scanning…";
  try {
    const data = Object.fromEntries(new FormData(form));
    const result = await request("/api/scan", data);
    showJobs(result.jobs, `${result.jobs.length} job${result.jobs.length === 1 ? "" : "s"} reviewed one by one`);
    result.errors.forEach(error => reportError(`${error.url}: ${error.message}`));
  } catch (error) {
    workspace.hidden = false;
    alerts.replaceChildren();
    jobList.replaceChildren();
    reportError(error.message);
  } finally {
    button.disabled = false;
    button.querySelector("span").textContent = "Scan listings";
  }
});

document.querySelector("#job-button").addEventListener("click", async event => {
  const button = event.currentTarget;
  const input = document.querySelector("#job-url");
  if (!input.reportValidity()) return;
  button.disabled = true;
  button.textContent = "Fetching…";
  try {
    const result = await request("/api/job", {url: input.value});
    showJobs([result.job], "Single job page");
  } catch (error) {
    workspace.hidden = false;
    alerts.replaceChildren();
    reportError(error.message);
  } finally {
    button.disabled = false;
    button.textContent = "Fetch job ↗";
  }
});

async function showHistory() {
  historyPanel.hidden = false;
  historyList.replaceChildren();
  try {
    const response = await fetch("/api/history");
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Could not load history.");
    document.querySelector("#history-clear").disabled = result.history.length === 0;
    if (!result.history.length) historyList.append(node("div", "empty-state", "No local history yet."));
    result.history.forEach(item => {
      const row = node("div", "history-item");
      const details = node("div");
      details.append(node("strong", "", item.title));
      const historyUrl = safeExternalUrl(item.url);
      if (historyUrl) {
        const link = node("a", "", item.url);
        link.href = historyUrl;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        details.append(link);
      } else {
        details.append(node("span", "", "Source URL unavailable"));
      }
      const actions = node("div", "history-item-actions");
      actions.append(node("time", "", new Date(item.created_at * 1000).toLocaleDateString()));
      const deleteButton = node("button", "delete-history-button", "Delete");
      deleteButton.type = "button";
      deleteButton.setAttribute("aria-label", `Delete local history for ${item.title}`);
      deleteButton.addEventListener("click", async () => {
        if (!window.confirm(`Delete "${item.title}" from local history? The Blogger post will not be deleted.`)) return;
        deleteButton.disabled = true;
        try {
          await request("/api/history/delete", {url: item.url});
          row.remove();
          if (!historyList.querySelector(".history-item")) {
            historyList.replaceChildren(node("div", "empty-state", "No local history yet."));
            document.querySelector("#history-clear").disabled = true;
          }
        } catch (error) {
          deleteButton.disabled = false;
          historyList.append(node("div", "alert", error.message));
        }
      });
      actions.append(deleteButton);
      row.append(details, actions);
      historyList.append(row);
    });
  } catch (error) {
    historyList.append(node("div", "alert", error.message));
  }
  historyPanel.scrollIntoView({behavior: "smooth", block: "start"});
}

document.querySelector("#history-toggle").addEventListener("click", showHistory);
document.querySelector("#history-close").addEventListener("click", () => { historyPanel.hidden = true; });
document.querySelector("#history-clear").addEventListener("click", async event => {
  const button = event.currentTarget;
  if (!window.confirm("Clear all local history? Blogger posts will not be deleted, and these jobs may be imported again.")) return;
  button.disabled = true;
  try {
    await request("/api/history/clear", {});
    historyList.replaceChildren(node("div", "empty-state", "No local history yet."));
  } catch (error) {
    historyList.append(node("div", "alert", error.message));
    button.disabled = false;
  }
});
