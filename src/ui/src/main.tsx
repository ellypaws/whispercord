import { render } from "preact";
import { App } from "./app";
import "./styles.css";
import "./legacy-app.js";

const mount = document.createElement("div");
mount.id = "preact-root";
mount.hidden = true;
document.body.appendChild(mount);

render(<App />, mount);
