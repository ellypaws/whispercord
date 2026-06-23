const html = "  <section id=\"v-speakers\" class=\"view\">\n    <div class=\"card\">\n      <h2>Speakers</h2>\n      <div id=\"speakers\"><div class=\"empty\">Active speakers appear here once the engine is running.</div></div>\n      <div class=\"hint\" style=\"margin:8px 0 0\">Click a speaker (here or in the Transcript) to assign them from the call roster, or type a name / paste a user ID if they aren't listed. Manual picks stay locked until you clear them.</div>\n    </div>\n  </section>";

export function SpeakersView() {
  return <div style={{ display: "contents" }} dangerouslySetInnerHTML={{ __html: html }} />;
}
