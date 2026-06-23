const html = "  <section id=\"v-live\" class=\"view active\">\n    <div id=\"transcript\"><div class=\"empty\">Waiting for speech… start the engine and join a voice call.</div></div>\n  </section>";

export function TranscriptView() {
  return <div style={{ display: "contents" }} dangerouslySetInnerHTML={{ __html: html }} />;
}
