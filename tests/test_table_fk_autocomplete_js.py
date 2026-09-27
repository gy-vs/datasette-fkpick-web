"""
Tests for the foreign key autocomplete in datasette/static/table.js,
using the same node + vm fake-DOM pattern as test_navigation_search_js.py
"""

import json
from pathlib import Path
import subprocess
import textwrap

REPO_ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = REPO_ROOT / "datasette" / "static"


def test_foreign_key_autocomplete_keyboard_and_current_value():
    script = textwrap.dedent("""
        const fs = require("fs");
        const vm = require("vm");
        const tableJs = __TABLE_JS__;

        class FakeTextNode {
          constructor(text) {
            this.textContent = text;
            this.parentNode = null;
          }
        }

        class FakeElement {
          constructor(tagName) {
            this.tagName = tagName.toUpperCase();
            this.children = [];
            this.parentNode = null;
            this.listeners = {};
            this.attrs = {};
            this.className = "";
            this.hidden = false;
            this.value = "";
            this._text = "";
            const self = this;
            this.classList = {
              add(name) {
                if (!self._hasClass(name)) {
                  self.className = (self.className + " " + name).trim();
                }
              },
              remove(name) {
                self.className = self.className
                  .split(" ")
                  .filter((c) => c && c !== name)
                  .join(" ");
              },
              toggle(name, force) {
                if (typeof force === "undefined") {
                  force = !self._hasClass(name);
                }
                if (force) {
                  this.add(name);
                } else {
                  this.remove(name);
                }
              },
              contains(name) {
                return self._hasClass(name);
              },
            };
          }
          set textContent(value) {
            this.children = [];
            this._text = String(value);
          }
          get textContent() {
            return (
              this._text +
              this.children.map((child) => child.textContent || "").join("")
            );
          }
          set innerHTML(value) {
            this.children = [];
            this._text = "";
          }
          get innerHTML() {
            return "";
          }
          _hasClass(name) {
            return this.className.split(" ").includes(name);
          }
          _matches(selector) {
            if (selector.startsWith(".")) {
              return selector
                .slice(1)
                .split(".")
                .every((name) => this._hasClass(name));
            }
            return this.tagName === selector.toUpperCase();
          }
          querySelectorAll(selector) {
            const matches = [];
            const walk = (el) => {
              for (const child of el.children) {
                if (child._matches && child._matches(selector)) {
                  matches.push(child);
                }
                if (child.children) {
                  walk(child);
                }
              }
            };
            walk(this);
            return matches;
          }
          querySelector(selector) {
            return this.querySelectorAll(selector)[0] || null;
          }
          appendChild(child) {
            if (child.parentNode) {
              const siblings = child.parentNode.children;
              const index = siblings.indexOf(child);
              if (index !== -1) {
                siblings.splice(index, 1);
              }
            }
            child.parentNode = this;
            this.children.push(child);
            return child;
          }
          insertBefore(node, reference) {
            if (node.parentNode) {
              const siblings = node.parentNode.children;
              const index = siblings.indexOf(node);
              if (index !== -1) {
                siblings.splice(index, 1);
              }
            }
            node.parentNode = this;
            const index = this.children.indexOf(reference);
            if (index === -1) {
              this.children.push(node);
            } else {
              this.children.splice(index, 0, node);
            }
            return node;
          }
          setAttribute(name, value) {
            this.attrs[name] = value;
          }
          getAttribute(name) {
            return this.attrs[name] || null;
          }
          addEventListener(type, fn) {
            this.listeners[type] = this.listeners[type] || [];
            this.listeners[type].push(fn);
          }
          focus() {}
          scrollIntoView() {}
        }

        function dispatch(element, type, event) {
          event.target = element;
          let node = element;
          while (node) {
            (node.listeners[type] || []).forEach((fn) => fn(event));
            if (event.propagationStopped) {
              break;
            }
            node = node.parentNode;
          }
          return event;
        }

        function keyEvent(key) {
          return {
            key,
            defaultPrevented: false,
            propagationStopped: false,
            preventDefault() {
              this.defaultPrevented = true;
            },
            stopPropagation() {
              this.propagationStopped = true;
            },
          };
        }

        const pendingTimers = [];
        const fetchCalls = [];

        global.document = {
          createElement(tag) {
            return new FakeElement(tag);
          },
          createTextNode(text) {
            return new FakeTextNode(text);
          },
          addEventListener() {},
          body: new FakeElement("body"),
        };
        global.window = {
          setTimeout(fn) {
            pendingTimers.push(fn);
            return pendingTimers.length;
          },
          clearTimeout() {},
        };
        global.location = { href: "http://localhost/data/orders" };
        global.fetch = async (url, options) => {
          fetchCalls.push(url);
          const q = new URL(url).searchParams.get("q");
          if (q === "err") {
            throw new Error("network down");
          }
          let results = [];
          if (q === "ali") {
            results = [
              { value: 12, label: "Alice", url: "/data/customers/12" },
              { value: 112, label: "Alice Two", url: "/data/customers/112" },
            ];
          } else if (q === "12") {
            results = [{ value: 12, label: "Alice", url: "/data/customers/12" }];
          }
          return {
            ok: true,
            json: async () => ({ ok: true, results, timed_out: false }),
          };
        };

        vm.runInThisContext(fs.readFileSync(tableJs, "utf8"), {
          filename: "table.js",
        });

        function flushTimers() {
          while (pendingTimers.length) {
            pendingTimers.shift()();
          }
        }
        async function tick() {
          await new Promise((resolve) => setImmediate(resolve));
        }
        function assert(condition, message) {
          if (!condition) {
            throw new Error("Assertion failed: " + message);
          }
        }

        const foreignKey = {
          table: "customers",
          column: "id",
          url: "/data/orders/-/foreign-key-suggestions?column=customer_id",
        };

        async function main() {
          // Parent element stands in for the dialog, to prove Escape does
          // not bubble up and close it while the dropdown is open
          const dialog = new FakeElement("dialog");
          let dialogSawEscape = 0;
          dialog.addEventListener("keydown", (ev) => {
            if (ev.key === "Escape") {
              dialogSawEscape += 1;
            }
          });
          const controlWrap = new FakeElement("div");
          dialog.appendChild(controlWrap);
          const control = new FakeElement("input");
          controlWrap.appendChild(control);

          attachForeignKeyAutocomplete(control, controlWrap, foreignKey);

          const wrapper = controlWrap.children[0];
          const listbox = wrapper.children[1];
          const current = controlWrap.children[1];
          assert(listbox.hidden, "listbox starts hidden");
          assert(current.hidden, "current note starts hidden");

          // Type "ali" - suggestions appear
          control.value = "ali";
          dispatch(control, "input", keyEvent(""));
          flushTimers();
          await tick();
          await tick();
          assert(!listbox.hidden, "listbox opens after typing");
          assert(
            fetchCalls.some((url) => url.includes("q=ali")),
            "fetch called with q=ali, got: " + fetchCalls.join(","),
          );
          const items = listbox.querySelectorAll(".row-edit-fk-suggestion");
          assert(items.length === 2, "two suggestions rendered");
          assert(items[0].textContent.includes("Alice"), "first label shown");
          assert(items[0].textContent.includes("12"), "first value shown");
          assert(
            !items[0].classList.contains("active"),
            "nothing highlighted until arrow keys are used",
          );

          // Arrow keys move the highlight, wrapping around
          dispatch(control, "keydown", keyEvent("ArrowDown"));
          assert(items[0].classList.contains("active"), "first item active");
          dispatch(control, "keydown", keyEvent("ArrowDown"));
          assert(items[1].classList.contains("active"), "second item active");
          dispatch(control, "keydown", keyEvent("ArrowDown"));
          assert(items[0].classList.contains("active"), "highlight wraps");

          // Enter picks the highlighted row and writes the raw value
          const enterEvent = dispatch(control, "keydown", keyEvent("Enter"));
          assert(enterEvent.defaultPrevented, "Enter is captured");
          assert(control.value === "12", "raw value written to input");
          assert(listbox.hidden, "dropdown closes after selection");
          assert(!current.hidden, "current note appears after selection");
          assert(current.textContent.includes("Alice"), "current shows label");
          const link = current.querySelectorAll("a")[0];
          assert(
            link && link.href === "/data/customers/12",
            "current note links to the row page",
          );

          // Re-open, then Escape closes only the dropdown, not the dialog
          control.value = "ali";
          dispatch(control, "input", keyEvent(""));
          flushTimers();
          await tick();
          await tick();
          assert(!listbox.hidden, "dropdown reopens");
          const escapeEvent = dispatch(control, "keydown", keyEvent("Escape"));
          assert(escapeEvent.defaultPrevented, "Escape is captured");
          assert(escapeEvent.propagationStopped, "Escape does not bubble");
          assert(listbox.hidden, "dropdown closes on Escape");
          assert(dialogSawEscape === 0, "dialog did not see Escape");

          // Escape with the dropdown closed bubbles up to the dialog
          dispatch(control, "keydown", keyEvent("Escape"));
          assert(dialogSawEscape === 1, "dialog sees Escape when closed");

          // Enter with no highlighted item submits the form as usual
          control.value = "ali";
          dispatch(control, "input", keyEvent(""));
          flushTimers();
          await tick();
          await tick();
          control.value = "99";
          const plainEnter = dispatch(control, "keydown", keyEvent("Enter"));
          assert(
            !plainEnter.defaultPrevented,
            "Enter without highlight is not captured",
          );
          assert(control.value === "99", "manually typed value is kept");

          // A failed fetch leaves the dialog working
          control.value = "err";
          dispatch(control, "input", keyEvent(""));
          flushTimers();
          await tick();
          await tick();
          assert(listbox.hidden, "dropdown stays closed on fetch error");

          // Clicking a suggestion with the mouse selects it
          control.value = "ali";
          dispatch(control, "input", keyEvent(""));
          flushTimers();
          await tick();
          await tick();
          const freshItems = listbox.querySelectorAll(".row-edit-fk-suggestion");
          const clickEvent = keyEvent("");
          dispatch(freshItems[1], "mousedown", clickEvent);
          assert(clickEvent.defaultPrevented, "mousedown keeps focus");
          assert(control.value === "112", "mouse selection writes raw value");

          // Edit mode: existing value resolves to its current label + link
          const wrap2 = new FakeElement("div");
          dialog.appendChild(wrap2);
          const control2 = new FakeElement("input");
          control2.value = "12";
          wrap2.appendChild(control2);
          attachForeignKeyAutocomplete(control2, wrap2, foreignKey);
          await tick();
          await tick();
          const current2 = wrap2.children[1];
          assert(!current2.hidden, "current note shown for existing value");
          assert(
            current2.textContent.includes("Alice"),
            "existing value resolves to label",
          );
          const link2 = current2.querySelectorAll("a")[0];
          assert(
            link2 && link2.href === "/data/customers/12",
            "existing value links to row page",
          );

          // Edit mode: a value with no matching row says so
          const wrap3 = new FakeElement("div");
          dialog.appendChild(wrap3);
          const control3 = new FakeElement("input");
          control3.value = "77";
          wrap3.appendChild(control3);
          attachForeignKeyAutocomplete(control3, wrap3, foreignKey);
          await tick();
          await tick();
          const current3 = wrap3.children[1];
          assert(
            current3.textContent.includes("no matching row in customers"),
            "dangling value is reported, got: " + current3.textContent,
          );

          process.stdout.write("ok");
        }

        main().catch((error) => {
          console.error(error);
          process.exit(1);
        });
        """).replace(
        "__TABLE_JS__",
        json.dumps(str(STATIC_DIR / "table.js")),
    )
    result = subprocess.run(
        ["node", "-e", script],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.endswith("ok")
