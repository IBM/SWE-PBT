#!/usr/bin/env python3

"""Extract model patches from trajectory files and write a preds.json file."""

import json
from pathlib import Path

import typer

app = typer.Typer(add_completion=False)


@app.command()
def main(
    output_dir: Path = typer.Argument(..., help="Directory containing *.traj.json trajectory files"),
) -> None:
    """Extract patches from trajectory files and write preds.json to the same directory."""
    if not output_dir.is_dir():
        typer.echo(f"Error: '{output_dir}' is not a directory", err=True)
        raise typer.Exit(code=1)

    preds: dict = {}
    for traj in sorted(output_dir.glob("*.traj.json")):
        iid = traj.name.removesuffix(".traj.json")
        if "__" not in iid:
            # skip non-instance files such as demorun.traj.json
            continue
        info = json.loads(traj.read_text()).get("info", {})
        preds[iid] = {
            "model_name_or_path": "mini-swe-agent",
            "instance_id": iid,
            "model_patch": info.get("submission", ""),
        }

    out = output_dir / "preds.json"
    out.write_text(json.dumps(preds, indent=2))
    typer.echo(f"Wrote {len(preds)} predictions to {out}")


if __name__ == "__main__":
    app()
