"""In-memory evolving taxonomy state (pure data; no I/O)."""
from __future__ import annotations
from copy import deepcopy
from pydantic import BaseModel, Field

from .protocol import EvolvingOp


class TaxonomyState(BaseModel):
    # categories: {category_name: {"definition": str}} (definition optional, may be "")
    categories: dict[str, dict] = Field(default_factory=dict)
    # subtypes: {subtype_code: {"name": str, "definition": str, "parent": str, ...}}
    subtypes: dict[str, dict] = Field(default_factory=dict)

    def size(self) -> int:
        return len(self.subtypes)

    def to_json(self) -> dict:
        return self.model_dump(mode="json")

    @classmethod
    def from_json(cls, d: dict) -> "TaxonomyState":
        return cls.model_validate(d)

    def apply_op(self, op_name: str, op_args: dict) -> "TaxonomyState":
        """Return a NEW TaxonomyState with the op applied. Self is unchanged."""
        new = TaxonomyState(
            categories=deepcopy(self.categories),
            subtypes=deepcopy(self.subtypes),
        )
        if op_name == EvolvingOp.REUSE:
            code = op_args["subtype_code"]
            if code not in new.subtypes:
                raise ValueError(f"REUSE references unknown subtype {code}")
        elif op_name == EvolvingOp.DISCOVER_APPEND:
            parent = op_args["parent_category"]
            code = op_args["new_subtype_code"]
            if code in new.subtypes:
                raise ValueError(f"DISCOVER_APPEND duplicate subtype {code}")
            if parent not in new.categories:
                new.categories[parent] = {"definition": ""}
            new.subtypes[code] = {
                "name": op_args["name"],
                "definition": op_args["definition"],
                "parent": parent,
            }
        elif op_name == EvolvingOp.EDIT_RENAME:
            # Locked decision: "rename" refines DEFINITION; code stays the same.
            code = op_args["subtype_code"]
            if code not in new.subtypes:
                raise ValueError(f"EDIT_RENAME references unknown subtype {code}")
            new.subtypes[code]["definition"] = op_args["new_definition"]
        elif op_name == EvolvingOp.EDIT_SPLIT:
            code = op_args["subtype_code"]
            if code not in new.subtypes:
                raise ValueError(f"EDIT_SPLIT references unknown subtype {code}")
            parent = new.subtypes[code]["parent"]
            del new.subtypes[code]
            for child in op_args["new_subtypes"]:
                new.subtypes[child["new_code"]] = {
                    "name": child["name"],
                    "definition": child["definition"],
                    "parent": parent,
                    "split_from": code,
                }
        elif op_name == EvolvingOp.EDIT_MERGE:
            sources = op_args["source_codes"]
            missing = [c for c in sources if c not in new.subtypes]
            if missing:
                raise ValueError(f"EDIT_MERGE references unknown subtypes {missing}")
            parent = new.subtypes[sources[0]]["parent"]
            for c in sources:
                del new.subtypes[c]
            new.subtypes[op_args["new_code"]] = {
                "name": op_args["name"],
                "definition": op_args["definition"],
                "parent": parent,
                "merged_from": list(sources),
            }
        else:
            raise ValueError(f"Unknown op {op_name}")
        return new
