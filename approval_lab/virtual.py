"""Read-only fixture interpreter. Never starts a shell or accesses the host filesystem."""

import ast
import fnmatch
import json
import posixpath
import re
import shlex


class EvidenceError(ValueError):
    pass


class VirtualEnvironment:
    def __init__(self, scene):
        self.cwd = scene.cwd
        self.files = {self.absolute(path): content for path, content in scene.files.items()}
        self.links = {self.absolute(path): self.absolute(target) for path, target in scene.links.items()}
        self.git = scene.git
        self.calls = []

    def absolute(self, path, cwd=None):
        path = str(path).replace("\\", "/")
        if path.startswith("~"):
            path = "/home/labuser" + path[1:]
        return posixpath.normpath(path if path.startswith("/") else posixpath.join(cwd or self.cwd, path))

    def resolve(self, path, cwd=None):
        path = self.absolute(path, cwd)
        for _ in range(16):
            match = next((link for link in sorted(self.links, key=len, reverse=True)
                          if path == link or path.startswith(link + "/")), None)
            if match is None:
                return path
            path = self.absolute(self.links[match] + path[len(match):])
        raise EvidenceError("Symbolic-link loop in fixture")

    def exists(self, path):
        path = self.resolve(path)
        return path in self.files or any(name.startswith(path.rstrip("/") + "/") for name in self.files)

    def read(self, path, cwd=None):
        path = self.resolve(path, cwd)
        if path not in self.files:
            raise EvidenceError(f"No such fixture file: {path}; no host files were read")
        return self.files[path]

    def inspect(self, path, cwd=None):
        literal, resolved = self.absolute(path, cwd), self.resolve(path, cwd)
        children = [name for name in self.files if name.startswith(resolved.rstrip("/") + "/")]
        if resolved not in self.files and not children and literal not in self.links:
            raise EvidenceError(f"No such fixture path: {literal}")
        return {"path": literal, "resolved_path": resolved, "is_symlink": literal in self.links,
                "link_target": self.links.get(literal), "type": "file" if resolved in self.files else "directory",
                "size": len(self.files.get(resolved, "").encode()), "children": sorted(children)[:100]}

    def call(self, name, arguments):
        short = name.rsplit(".", 1)[-1]
        try:
            if short == "write_stdin":
                raise EvidenceError("No running fixture session; all read-only fixture queries finish synchronously")
            if short != "exec_command":
                raise EvidenceError(f"Unknown fixture tool: {name}")
            if any(key in arguments for key in ("sandbox_permissions", "additional_permissions", "prefix_rule")):
                raise EvidenceError("Guardian may not request permission overrides")
            command = arguments.get("cmd")
            if not isinstance(command, str) or len(command) > 20000:
                raise EvidenceError("Expected a read-only cmd string (maximum 20000 characters)")
            cwd = self.absolute(arguments.get("workdir") or self.cwd)
            output = self.query(command, cwd)
            budget = max(100, min(int(arguments.get("max_output_tokens", 10000)), 10000)) * 4
            if len(output) > budget:
                output = output[:budget] + "\n<truncated reason=fixture-output-budget />"
            result = {"wall_time_seconds": 0, "exit_code": 0, "output": output}
        except (EvidenceError, ValueError, TypeError, KeyError, re.error) as exc:
            result = {"wall_time_seconds": 0, "exit_code": 1, "output": f"Fixture query error: {exc}"[:2000]}
        self.calls.append({"name": name, "arguments": arguments, "result": result})
        return result

    def query(self, command, cwd):
        # Explicitly reject shell execution semantics rather than pretending to execute.
        if re.search(r"`|\$\(|\$\{|\beval\b|\bexec\b|[<>]", command):
            raise EvidenceError("Unsupported shell expansion/execution/redirection; use cat, ls, stat, readlink, realpath, rg or git")
        parts = re.split(r"\s*(?:&&|;)\s*", command)
        if len(parts) > 1:
            if len(parts) > 12:
                raise EvidenceError("Too many fixture queries")
            outputs = []
            for part in parts:
                if part.startswith("cd "):
                    cwd = self.resolve(shlex.split(part)[1], cwd)
                elif part.strip():
                    outputs.append(self.query(part.strip(), cwd))
            return "\n".join(outputs)
        if "|" in command:
            raise EvidenceError("Pipelines are not implemented in this fixture interpreter; issue separate read-only queries")
        args = shlex.split(command)
        if not args:
            raise EvidenceError("Empty command")
        tool = args[0].lower()
        rest = args[1:]
        if tool in ("pwd", "get-location"):
            return cwd
        if tool in ("cat", "get-content", "type"):
            paths = [item for item in rest if not item.startswith("-")]
            if tool == "get-content":
                paths = [rest[i+1] for i, arg in enumerate(rest[:-1]) if arg.lower() in ("-path", "-literalpath")] or paths[:1]
            if not paths:
                raise EvidenceError("Specify a file path")
            return "\n".join(self.read(path, cwd) for path in paths)
        if tool in ("readlink", "realpath", "resolve-path"):
            path = next((x for x in rest if not x.startswith("-")), ".")
            return self.resolve(path, cwd)
        if tool in ("stat", "get-item", "test-path"):
            path = next((x for x in reversed(rest) if not x.startswith("-")), ".")
            if tool == "test-path":
                return str(self.exists(self.absolute(path, cwd))).lower()
            return json.dumps(self.inspect(path, cwd), ensure_ascii=False)
        if tool in ("ls", "find", "get-childitem", "dir"):
            paths = [x for x in rest if not x.startswith("-")]
            root = self.resolve(paths[0] if paths else ".", cwd)
            # ls -ld must show the link itself as well as its resolved target.
            link = self.absolute(paths[0], cwd) if paths else cwd
            entries = [json.dumps(self.inspect(link, cwd))] if link in self.links or root in self.files else []
            entries += [f"{name}\t{len(content.encode())} bytes" for name, content in sorted(self.files.items())
                        if name.startswith(root.rstrip("/") + "/")]
            if not entries and not self.exists(root):
                raise EvidenceError(f"No such fixture directory: {root}")
            return "\n".join(entries) or "(empty fixture directory)"
        if tool == "sed":
            values = [arg for arg in rest if not arg.startswith("-")]
            if len(values) != 2 or not re.fullmatch(r"\d+(,\d+)?p", values[0]):
                raise EvidenceError("Supported sed query: sed -n '1,160p' file")
            indices = [int(x) for x in values[0][:-1].split(",")]
            return "\n".join(self.read(values[1], cwd).splitlines()[indices[0]-1:indices[-1]])
        if tool in ("rg", "grep"):
            if "--files" in rest:
                return "\n".join(sorted(self.files))
            values = [x for x in rest if not x.startswith("-")]
            if not values:
                raise EvidenceError("Specify a pattern")
            pattern, paths = values[0], values[1:]
            matches = []
            for path, content in sorted(self.files.items()):
                if paths and not any(path == self.resolve(p, cwd) or path.startswith(self.resolve(p, cwd) + "/")
                                     or fnmatch.fnmatch(path, self.absolute(p, cwd)) for p in paths):
                    continue
                for index, line in enumerate(content.splitlines(), 1):
                    if re.search(pattern, line, re.IGNORECASE if "-i" in rest else 0):
                        matches.append(f"{path}:{index}:{line}")
            return "\n".join(matches) or "(no fixture matches)"
        if tool == "git":
            key = " ".join(rest)
            if key in self.git:
                return self.git[key]
            if key in ("status", "status --short", "diff", "diff --stat", "log -5 --oneline", "branch --show-current", "remote -v"):
                raise EvidenceError(f"No git evidence supplied for {key}; absence does not establish a clean/trusted repository")
            raise EvidenceError("Only explicitly supplied read-only git queries are supported")
        if tool in ("python", "python3") and len(rest) == 2 and rest[0] == "-c":
            # Parse a small useful read pattern; never eval/exec even a restricted string.
            tree = ast.parse(rest[1])
            matches = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
                       and isinstance(node.func, ast.Attribute) and node.func.attr in ("read_text", "resolve", "exists")]
            if len(matches) == 1:
                node = matches[0]
                path_call = node.func.value
                if isinstance(path_call, ast.Call) and len(path_call.args) == 1 and isinstance(path_call.args[0], ast.Constant):
                    # A read is accepted only when no other calls besides Path/print appear.
                    calls = [c for c in ast.walk(tree) if isinstance(c, ast.Call)]
                    if all(c is node or c is path_call or isinstance(c.func, ast.Name) and c.func.id == "print" for c in calls):
                        path = path_call.args[0].value
                        if not isinstance(path, str):
                            raise EvidenceError("Path must be a literal string")
                        if node.func.attr == "read_text":
                            return self.read(path, cwd)
                        if node.func.attr == "resolve":
                            return self.resolve(path, cwd)
                        return str(self.exists(self.absolute(path, cwd)))
            raise EvidenceError("Supported Python fixture query: print(Path('literal').read_text()/resolve()/exists())")
        raise EvidenceError(f"Unsupported fixture query {tool!r}; no command was executed. Use cat/ls/stat/readlink/realpath/rg/git.")
