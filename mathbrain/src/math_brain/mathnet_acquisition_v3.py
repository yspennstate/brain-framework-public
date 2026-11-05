"""Byte-verified acquisition of the one pinned MathNet source used by study v3.

This is the trust boundary between Hugging Face/Parquet and the private bank and pilot
material builders.
The public entry point has no repository, revision, manifest-digest, or row-adapter
arguments: those values are compiled into this module and checked against both remote
metadata and local bytes.  Downloading is opt-in; cache-only operation is the default.

The module deliberately imports neither ``datasets`` nor PIL.  Image *paths* are projected
only to retain list cardinality and are replaced with null sentinels before a row crosses
into the bank layer.  Problem, solution, answer, source-id, and image-path values never
enter the public source-artifact manifest.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from .models import canonical_json

MATHNET_REPOSITORY_V3 = "ShadenA/MathNet"
MATHNET_REVISION_V3 = "33e6b3bc254e6f0f0c1479b4252f5e9dd551d56c"
MATHNET_CONFIG_V3 = "all"
MATHNET_SPLIT_V3 = "train"
MATHNET_SHARD_COUNT_V3 = 56
MATHNET_TOTAL_BYTES_V3 = 353_156_621
MATHNET_TOTAL_ROWS_V3 = 27_817
MATHNET_SHARD_ROWS_V3 = (497,) * 41 + (496,) * 15

# Key-independent full-source pilot pins measured by two complete cache-only build/replay
# integrations on 2026-07-15.  The vault key and every vault-private commitment are
# deliberately absent.  Changing any screening or coordinate semantics requires a new
# protocol version rather than silently changing this v3 universe.
EXPECTED_MATHNET_PILOT_CANDIDATE_COUNT_V3 = 1_029
EXPECTED_MATHNET_PILOT_SCREEN_COUNTS_V3 = (
    ("source_english_eligible", 87),
    ("null_language_audit_queue", 942),
    ("non_english", 1_246),
    ("missing_source_metadata", 434),
    ("non_text_only", 4_916),
    ("unsafe_prompt_markup", 0),
    ("unsupported_problem_type", 19_622),
    ("missing_answer", 17),
    ("missing_solution_evidence", 165),
    ("unsupported_answer", 384),
    ("missing_solution_confirmation", 0),
    ("solution_disagreement", 0),
    ("text_budget_exceeded", 4),
)
EXPECTED_MATHNET_PILOT_UNIVERSE_COMMITMENTS_V3 = (
    (
        "coordinate_universe_digest",
        "sha256:c21923c82336c98cfb918de35265f86aaa688372618e72ced6f5ec3a86452239",
    ),
    (
        "gold_free_ordered_rows_digest",
        "sha256:cca0f1023687435bd4fddcc10c2832b2993f20fd32c7671d41ff563832beb65e",
    ),
    (
        "public_ordered_source_rows_digest",
        "sha256:7e8d2a0661aa545d82891b5056ec993fc38e3941fc6e2208e69e76a9a17b45c6",
    ),
    (
        "universe_content_digest",
        "sha256:8b64a4063101ebc3e5cee9ce0278e90d58d780a5ce89a9c389ffe9ef4df694d6",
    ),
    (
        "public_content_digest",
        "sha256:d9961eccd4a34ec5b34ec739704778915d5b67caccc68f0625affbc8eb059c7f",
    ),
)

MATHNET_ROW_PROJECTION_V3 = (
    "id",
    "problem_markdown",
    "solutions_markdown",
    "images.list.element.path",
    "country",
    "competition",
    "topics_flat",
    "language",
    "problem_type",
    "final_answer",
)

_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_SHA1_RE = re.compile(r"[0-9a-f]{40}\Z")
_LANE_PATH_RE = re.compile(r"data/all/train-(?P<ordinal>[0-9]{5})-of-00056\.parquet\Z")
_MANIFEST_DOMAIN = "org.math-research-brain/mathnet-source-artifact-manifest/v3"
_ARROW_SCHEMA_DOMAIN = "org.math-research-brain/mathnet-arrow-schema/v3"
_ROW_ADAPTER_DOMAIN = "org.math-research-brain/mathnet-row-adapter/v3"
_OFFICIAL_PILOT_UNIVERSE_DOMAIN = (
    "org.math-research-brain/mathnet-official-pilot-universe/v3"
)
_OFFICIAL_PILOT_EVALUATOR_VAULT_DOMAIN = (
    "org.math-research-brain/mathnet-official-pilot-evaluator-vault/v3"
)


def _digest(value: object, *, domain: str) -> str:
    return "sha256:" + hashlib.sha256(
        canonical_json({"domain": domain, "payload": value}).encode("utf-8")
    ).hexdigest()


def _validate_sha256(value: object, label: str) -> str:
    if type(value) is not str or _SHA256_RE.fullmatch(value) is None:
        raise ValueError(f"{label} must be 64 lowercase SHA-256 hex characters")
    return value


def _validate_digest(value: object, label: str) -> str:
    if type(value) is not str or not value.startswith("sha256:"):
        raise ValueError(f"{label} must be a canonical SHA-256 digest")
    _validate_sha256(value.removeprefix("sha256:"), label)
    return value


def _lfs_pointer_git_oid(sha256_hex: str, size_bytes: int) -> str:
    _validate_sha256(sha256_hex, "LFS object digest")
    if type(size_bytes) is not int or size_bytes <= 0:
        raise ValueError("LFS object size must be a positive exact integer")
    pointer = (
        "version https://git-lfs.github.com/spec/v1\n"
        f"oid sha256:{sha256_hex}\n"
        f"size {size_bytes}\n"
    ).encode("ascii")
    return hashlib.sha1(
        b"blob " + str(len(pointer)).encode("ascii") + b"\0" + pointer
    ).hexdigest()


_MATHNET_SHARD_BYTE_PINS_V3 = (
    (21_957_999, "5019883c92fc14ff9861f3912856ec27440951c4323eba7a0aad8afd1638aa12"),
    (4_555_628, "260b68876e1b9fe31afa5ea3b4f2aca34d743f248fbe2148bb5988cc9309b25b"),
    (5_487_061, "3f7f91611e10b5856a04bda8a2894322fda226463dcb911520dc0b0755c62d75"),
    (6_354_774, "9982df4953a605ffb109eccb20f1ac55f54f10ebb2dbd5d05f14b900424ca2c2"),
    (8_175_307, "510e1dc95facb7354abe770e7ce8dfcc578704c5e75ce5094f64a8ec8503afdd"),
    (6_931_017, "26facbfae3f5a739c4007225261b32b535fb20d7a1d0e2cd45c16e1ade3e775f"),
    (6_693_711, "de9dfe94eec8eb77048a66c4bf5802f86db2af05a7c20a2ae41a60fa8a89fc2d"),
    (6_514_993, "575118c90270180257183327882eecda963e197ce897da0e3a0214fea27d37c9"),
    (4_805_452, "5565aa12548f47ed28b0b0302e1857d9796f9f8668883b36604384f044f09112"),
    (5_731_877, "9226d9c0c15515672f25890aca8475eb679338393dff496da8912a08db688d61"),
    (4_847_939, "36797db9fbfb2b138a523dc1a110471b389bebe2d0d18032ebe102e37323b565"),
    (6_402_899, "39d14c319f2d1afcccb2909e024070f421c3db75eab017e2d43e4d53d521e73a"),
    (7_826_523, "a72c22eec83b400579a2838aa6f62e5dbf25d433b761375f679ebc8d7430b916"),
    (4_306_686, "88fafe9b59a7cc95efe617eccec4ee6b577f5f832ed7743b53efc4aff55c6274"),
    (5_009_080, "f09f1cc54ef6e19ded383a7c15836fd06b88ab8208fccc457d32df1f03a00507"),
    (5_478_126, "b49771aabed03bf6c8d447ecf0793855d6f15a3a2d04ad17f756f3dcb9ada3bf"),
    (6_664_070, "fc607b08b2e438c38b88ed19a9a127c48e09456d500c6f9001bb6eb82e7b8af7"),
    (5_473_618, "732f68483be946c276ba062560ecd111c0a6a7e6d598555c7f46782b7ceb781a"),
    (4_985_840, "9afdfc7fdfdf86c56e40762a93e7a120c1b864cb9dbb024d413d43ea5689e494"),
    (5_247_460, "d062e7de3d902bb66159cbb8ce8c5d6590823156b4e025ed931d25de799f2e7f"),
    (5_732_035, "3d60c39b1b0d9eea919c74d96fe5040cd6002eefc0ced2e2c9436f0bcebaa524"),
    (7_056_512, "1f60f6a8671711775dcbb4d3d9e9ff54a1a8ea79471b8c59363e7ef8cb8a7eec"),
    (5_282_497, "08655f1c286fa3060fcdd3ebe2767adde5357c49b7f4d5d0395e7c2f80475a3d"),
    (6_164_521, "ea30fcb25c4a27361fffdb8f3e5f7da22be944bd5f70d96c86fe3e7151b9e1dc"),
    (7_234_442, "c2da58c2fa4348dd5a0285066e330b891b77894f5d3dd552ca036bce09776609"),
    (6_964_101, "872e6d99dec580f16d90a2aa0961bef9ad7119216f2bdd2d902171e3ecf82fc9"),
    (4_960_529, "77371f607b6723f05a62f99ce8477ca7387746d6da996175381d28dcc3046e54"),
    (5_870_831, "c995ffa3f3536641f361d866c615eb6c40ecbcadc69a0b969255d4e45f3ff03a"),
    (6_450_728, "3c3609f9627f28a1cabe060563692b69b9ab6c85fd31f3c2912334a87ae8dec8"),
    (6_212_666, "926c124871ec1f3f6deb944fc9d06d01bf038dd66358184d90c8f6bdebbe8bee"),
    (5_586_968, "4559e0a0769d800355c37b57501bddb29b20441a8df69c250c22689c49e1b1f0"),
    (6_271_953, "da9190b08cee3569e242a852ce30d332592efb27651d19b5bbf59e6023d122af"),
    (4_692_199, "3b06c6cc3d53729c928e3353936db5913b0f5575b244c8563791bfab1626b411"),
    (6_182_075, "86cbad9177868dd7d37c60f262fe125d3356ffed001ada34fd65a2984f341e55"),
    (5_957_711, "9a8132496d8ee9dcf6f162814d0c44c17652db6edaf1fdd8d5d624213d58ee61"),
    (6_621_172, "b726f4629559c8893638fadb1dea7834d53668b9d780f8dc2e5876b2907a7603"),
    (6_767_948, "47da921e939b2f494cdce40c331627b366e6765d032637bbf98b30a3f7e90538"),
    (5_695_788, "ad16919417cbf25d4a959dea2bfa1b391888ff6fbb48270e2805044363fcf190"),
    (5_634_107, "6f33d418680f5cb0a2732204bb4e1d9b09276b2b17f6fdaa9fd6bd91b7311716"),
    (5_080_763, "370362bd0e23ea492ff9e71da42ddeb91d4fa42d373fab789fd3df1d7765c99e"),
    (5_871_805, "f5fce9d85f935b6893d6577463c3e57aad98470354e1942009d7326a57583a8b"),
    (6_074_275, "f3c4a7f57f000b0785698b2c5d28b93797bf04e531d05680445f32021796f642"),
    (5_973_958, "5222d60c6f44e31c511d88308843d692a591de728fd0acb825b3eddb0dbb00f1"),
    (7_599_726, "2d455a39682172db6acd6f1b0993495015b6d54aeeab6aae5296371e1a49c3bd"),
    (6_143_147, "57312b35fa659a6c0665fab3615c99468ea921c8b15c6919063ac14d563c98fb"),
    (4_955_943, "289e41ec27c51f5cc0e0c0c67e009d1f19c8800ab2c47d7ea86e3c30f94753b3"),
    (4_856_554, "2316eb7df35da3a58b2cc6384cb027dc49f1a55bd564b380c959399f7f5675ce"),
    (6_374_830, "68a0ee5424763ee968adcc7ba41b41562d91b1509540cf077a72fb833d10797b"),
    (6_911_099, "5e077eef41014623150048ddbc3e65d50b1d9438eef943bb253af74d97350b57"),
    (6_074_043, "25d16c8f66ca7a0fcdf8b5d88fee6721d09d0b61db911d61399ce4e21aed1395"),
    (6_168_165, "5cd0af75ade8e8f6f863573dc0c7fefc315d3d9313d71d7c20bdbb78b2edbbff"),
    (6_074_557, "79e4d12da87c2050fe2d7397b797a32ee76a629a5ec16c908bb8d92f8041b741"),
    (6_216_752, "dfd2649bb3c641355a016821f857a52c8f48fa879691909f4d9584d879d1a443"),
    (5_436_932, "5a367829c7ccf49e1984fead201278be20cc5e4568dbb38fda969bc2b2dd6e2f"),
    (7_576_293, "bd384c79913d3f27b75dd8021f0dcb8be1a3234e23668a6a21386b41b87b74a4"),
    (6_978_936, "10059c6b44ef1e3135d2e731f77360e8675dc74de1532ba0500d3a141520a64f"),
)


@dataclass(frozen=True, slots=True)
class ExpectedShardV3:
    ordinal: int
    path: str
    size_bytes: int
    lfs_sha256: str
    row_count: int
    git_blob_oid: str = field(init=False)

    def __post_init__(self) -> None:
        if type(self) is not ExpectedShardV3:
            raise ValueError("MathNet shard subclasses are forbidden")
        if type(self.ordinal) is not int or not 0 <= self.ordinal < MATHNET_SHARD_COUNT_V3:
            raise ValueError("MathNet shard ordinal is invalid")
        expected_path = f"data/all/train-{self.ordinal:05d}-of-00056.parquet"
        if type(self.path) is not str or self.path != expected_path:
            raise ValueError("MathNet shard path is invalid")
        if type(self.size_bytes) is not int or self.size_bytes <= 0:
            raise ValueError("MathNet shard size must be a positive exact integer")
        _validate_sha256(self.lfs_sha256, "MathNet shard LFS digest")
        if type(self.row_count) is not int or self.row_count <= 0:
            raise ValueError("MathNet shard row count must be positive")
        object.__setattr__(
            self,
            "git_blob_oid",
            _lfs_pointer_git_oid(self.lfs_sha256, self.size_bytes),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "git_blob_oid": self.git_blob_oid,
            "lfs_sha256": self.lfs_sha256,
            "ordinal": self.ordinal,
            "path": self.path,
            "row_count": self.row_count,
            "size_bytes": self.size_bytes,
        }


EXPECTED_MATHNET_SHARDS_V3 = tuple(
    ExpectedShardV3(
        ordinal=ordinal,
        path=f"data/all/train-{ordinal:05d}-of-00056.parquet",
        size_bytes=size_bytes,
        lfs_sha256=lfs_sha256,
        row_count=MATHNET_SHARD_ROWS_V3[ordinal],
    )
    for ordinal, (size_bytes, lfs_sha256) in enumerate(_MATHNET_SHARD_BYTE_PINS_V3)
)


@dataclass(frozen=True, slots=True)
class PhysicalLeafV3:
    path: str
    physical_type: str
    logical_type: str
    max_definition_level: int
    max_repetition_level: int

    def __post_init__(self) -> None:
        if type(self) is not PhysicalLeafV3:
            raise ValueError("MathNet physical-leaf subclasses are forbidden")
        if any(type(item) is not str or not item for item in (
            self.path,
            self.physical_type,
            self.logical_type,
        )):
            raise ValueError("MathNet physical leaf text is invalid")
        if type(self.max_definition_level) is not int or type(
            self.max_repetition_level
        ) is not int:
            raise ValueError("MathNet physical levels must be exact integers")

    def to_dict(self) -> dict[str, object]:
        return {
            "logical_type": self.logical_type,
            "max_definition_level": self.max_definition_level,
            "max_repetition_level": self.max_repetition_level,
            "path": self.path,
            "physical_type": self.physical_type,
        }


MATHNET_PHYSICAL_LEAVES_V3 = tuple(
    PhysicalLeafV3(*values)
    for values in (
        ("id", "BYTE_ARRAY", "String", 1, 0),
        ("problem_markdown", "BYTE_ARRAY", "String", 1, 0),
        ("solutions_markdown.list.element", "BYTE_ARRAY", "String", 3, 1),
        ("images.list.element.bytes", "BYTE_ARRAY", "None", 4, 1),
        ("images.list.element.path", "BYTE_ARRAY", "String", 4, 1),
        ("country", "BYTE_ARRAY", "String", 1, 0),
        ("competition", "BYTE_ARRAY", "String", 1, 0),
        ("topics_flat.list.element", "BYTE_ARRAY", "String", 3, 1),
        ("language", "BYTE_ARRAY", "String", 1, 0),
        ("problem_type", "BYTE_ARRAY", "String", 1, 0),
        ("final_answer", "BYTE_ARRAY", "String", 1, 0),
    )
)


MATHNET_ARROW_SCHEMA_DESCRIPTOR_V3: tuple[dict[str, object], ...] = (
    {"name": "id", "nullable": True, "type": {"kind": "string"}},
    {"name": "problem_markdown", "nullable": True, "type": {"kind": "string"}},
    {
        "name": "solutions_markdown",
        "nullable": True,
        "type": {
            "kind": "list",
            "value": {"name": "element", "nullable": True, "type": {"kind": "string"}},
        },
    },
    {
        "name": "images",
        "nullable": True,
        "type": {
            "kind": "list",
            "value": {
                "name": "element",
                "nullable": True,
                "type": {
                    "fields": (
                        {"name": "bytes", "nullable": True, "type": {"kind": "binary"}},
                        {"name": "path", "nullable": True, "type": {"kind": "string"}},
                    ),
                    "kind": "struct",
                },
            },
        },
    },
    {"name": "country", "nullable": True, "type": {"kind": "string"}},
    {"name": "competition", "nullable": True, "type": {"kind": "string"}},
    {
        "name": "topics_flat",
        "nullable": True,
        "type": {
            "kind": "list",
            "value": {"name": "element", "nullable": True, "type": {"kind": "string"}},
        },
    },
    {"name": "language", "nullable": True, "type": {"kind": "string"}},
    {"name": "problem_type", "nullable": True, "type": {"kind": "string"}},
    {"name": "final_answer", "nullable": True, "type": {"kind": "string"}},
)

MATHNET_ARROW_SCHEMA_DIGEST_V3 = _digest(
    MATHNET_ARROW_SCHEMA_DESCRIPTOR_V3,
    domain=_ARROW_SCHEMA_DOMAIN,
)
MATHNET_ROW_ADAPTER_DIGEST_V3 = _digest(
    {
        "image_projection": "images.list.element.path",
        "image_value_policy": "replace-each-path-with-null-sentinel",
        "official_order": "shard,row-group,row",
        "projection": list(MATHNET_ROW_PROJECTION_V3),
        "threads": False,
    },
    domain=_ROW_ADAPTER_DOMAIN,
)


class MathNetCacheModeV3(StrEnum):
    REQUIRE_PRESENT = "require_present"
    DOWNLOAD_MISSING = "download_missing"


def verify_mathnet_remote_metadata_v3(info: object) -> tuple[ExpectedShardV3, ...]:
    """Verify a Hugging Face ``DatasetInfo`` without trusting its object types."""

    repository_id = getattr(info, "id", None)
    if type(repository_id) is not str or repository_id != MATHNET_REPOSITORY_V3:
        raise ValueError("MathNet repository metadata names another dataset")
    resolved = getattr(info, "sha", None)
    if type(resolved) is not str or resolved != MATHNET_REVISION_V3:
        raise ValueError("MathNet repository did not resolve to the pinned full revision")
    siblings = getattr(info, "siblings", None)
    if type(siblings) is not list:
        raise ValueError("MathNet repository metadata has no exact sibling list")

    lane: dict[int, object] = {}
    for sibling in siblings:
        path = getattr(sibling, "rfilename", None)
        if type(path) is not str:
            raise ValueError("MathNet repository contains a sibling without an exact path")
        match = _LANE_PATH_RE.fullmatch(path)
        if match is None:
            continue
        ordinal = int(match.group("ordinal"))
        if ordinal in lane:
            raise ValueError("MathNet repository metadata duplicates a lane ordinal")
        lane[ordinal] = sibling
    if tuple(sorted(lane)) != tuple(range(MATHNET_SHARD_COUNT_V3)):
        raise ValueError("MathNet repository metadata has a missing or extra lane shard")

    for expected in EXPECTED_MATHNET_SHARDS_V3:
        sibling = lane[expected.ordinal]
        lfs = getattr(sibling, "lfs", None)
        observed = (
            getattr(sibling, "rfilename", None),
            getattr(sibling, "size", None),
            getattr(sibling, "blob_id", None),
            getattr(lfs, "size", None),
            getattr(lfs, "sha256", None),
        )
        required = (
            expected.path,
            expected.size_bytes,
            expected.git_blob_oid,
            expected.size_bytes,
            expected.lfs_sha256,
        )
        required_types = (str, int, str, int, str)
        if any(
            type(item) is not expected_type
            for item, expected_type in zip(observed, required_types, strict=True)
        ) or observed != required:
            raise ValueError(f"MathNet shard {expected.ordinal} metadata differs from the pin")
        if _SHA1_RE.fullmatch(observed[2]) is None:
            raise ValueError("MathNet Git blob identity is not canonical")
    return EXPECTED_MATHNET_SHARDS_V3


@dataclass(frozen=True, slots=True)
class FooterAuditV3:
    ordinal: int
    row_count: int
    row_group_count: int
    physical_leaves: tuple[PhysicalLeafV3, ...]
    arrow_schema_digest: str
    created_by: str | None

    def __post_init__(self) -> None:
        if type(self) is not FooterAuditV3:
            raise ValueError("MathNet footer-audit subclasses are forbidden")
        if type(self.ordinal) is not int or not 0 <= self.ordinal < MATHNET_SHARD_COUNT_V3:
            raise ValueError("MathNet footer ordinal is invalid")
        if type(self.row_count) is not int or self.row_count != MATHNET_SHARD_ROWS_V3[
            self.ordinal
        ]:
            raise ValueError("MathNet footer row count differs from the pin")
        if type(self.row_group_count) is not int or self.row_group_count != 1:
            raise ValueError("MathNet shards must have exactly one row group")
        if (
            type(self.physical_leaves) is not tuple
            or not all(type(item) is PhysicalLeafV3 for item in self.physical_leaves)
            or self.physical_leaves != MATHNET_PHYSICAL_LEAVES_V3
        ):
            raise ValueError("MathNet physical Parquet schema differs from the pin")
        for item in self.physical_leaves:
            item.__post_init__()
        if type(self.arrow_schema_digest) is not str or (
            self.arrow_schema_digest != MATHNET_ARROW_SCHEMA_DIGEST_V3
        ):
            raise ValueError("MathNet recursive Arrow schema differs from the pin")
        if self.created_by is not None and type(self.created_by) is not str:
            raise ValueError("MathNet Parquet writer evidence must be text or null")

    def to_dict(self) -> dict[str, object]:
        return {
            "arrow_schema_digest": self.arrow_schema_digest,
            "created_by": self.created_by,
            "ordinal": self.ordinal,
            "physical_leaves": [item.to_dict() for item in self.physical_leaves],
            "row_count": self.row_count,
            "row_group_count": self.row_group_count,
        }


@dataclass(frozen=True, slots=True)
class _LocalShardFingerprintV3:
    size_bytes: int
    modified_ns: int
    device: int
    inode: int
    sha256: str

    def __post_init__(self) -> None:
        if type(self) is not _LocalShardFingerprintV3:
            raise ValueError("MathNet local-fingerprint subclasses are forbidden")
        if any(
            type(value) is not int or value < 0
            for value in (self.size_bytes, self.modified_ns, self.device, self.inode)
        ):
            raise ValueError("MathNet local-file fingerprint is invalid")
        _validate_sha256(self.sha256, "MathNet local-file digest")


@dataclass(frozen=True, slots=True)
class MathNetSourceArtifactManifestV3:
    repository_id: str
    revision: str
    config_name: str
    split: str
    shards: tuple[ExpectedShardV3, ...]
    footer_audits: tuple[FooterAuditV3, ...]
    total_bytes: int
    total_rows: int
    unique_id_count: int
    arrow_schema_digest: str
    row_adapter_digest: str
    content_digest: str = field(init=False)

    def __post_init__(self) -> None:
        if type(self) is not MathNetSourceArtifactManifestV3:
            raise ValueError("MathNet source-manifest subclasses are forbidden")
        self._validate_fields()
        object.__setattr__(
            self,
            "content_digest",
            _digest(self._payload(), domain=_MANIFEST_DOMAIN),
        )

    def _validate_fields(self) -> None:
        if (
            type(self.repository_id) is not str
            or self.repository_id != MATHNET_REPOSITORY_V3
            or type(self.revision) is not str
            or self.revision != MATHNET_REVISION_V3
            or type(self.config_name) is not str
            or self.config_name != MATHNET_CONFIG_V3
            or type(self.split) is not str
            or self.split != MATHNET_SPLIT_V3
        ):
            raise ValueError("MathNet source manifest names another source")
        if (
            type(self.shards) is not tuple
            or not all(type(item) is ExpectedShardV3 for item in self.shards)
            or self.shards != EXPECTED_MATHNET_SHARDS_V3
        ):
            raise ValueError("MathNet source manifest shard sequence differs from the pin")
        for item in self.shards:
            item.__post_init__()
        if type(self.footer_audits) is not tuple or len(self.footer_audits) != (
            MATHNET_SHARD_COUNT_V3
        ):
            raise ValueError("MathNet source manifest needs one footer audit per shard")
        for ordinal, audit in enumerate(self.footer_audits):
            if type(audit) is not FooterAuditV3 or audit.ordinal != ordinal:
                raise ValueError("MathNet footer audits are not exact and ordered")
            audit.__post_init__()
        if type(self.total_bytes) is not int or self.total_bytes != MATHNET_TOTAL_BYTES_V3:
            raise ValueError("MathNet source manifest byte count differs from the pin")
        if type(self.total_rows) is not int or self.total_rows != MATHNET_TOTAL_ROWS_V3:
            raise ValueError("MathNet source manifest row count differs from the pin")
        if type(self.unique_id_count) is not int or self.unique_id_count != (
            MATHNET_TOTAL_ROWS_V3
        ):
            raise ValueError("MathNet source IDs are not complete and unique")
        if type(self.arrow_schema_digest) is not str or (
            self.arrow_schema_digest != MATHNET_ARROW_SCHEMA_DIGEST_V3
        ):
            raise ValueError("MathNet source manifest Arrow schema is unsupported")
        if type(self.row_adapter_digest) is not str or (
            self.row_adapter_digest != MATHNET_ROW_ADAPTER_DIGEST_V3
        ):
            raise ValueError("MathNet source manifest row adapter is unsupported")
    def _payload(self) -> dict[str, object]:
        return {
            "arrow_schema_digest": self.arrow_schema_digest,
            "config_name": self.config_name,
            "footer_audits": [item.to_dict() for item in self.footer_audits],
            "repository_id": self.repository_id,
            "revision": self.revision,
            "row_adapter_digest": self.row_adapter_digest,
            "shards": [item.to_dict() for item in self.shards],
            "split": self.split,
            "total_bytes": self.total_bytes,
            "total_rows": self.total_rows,
            "unique_id_count": self.unique_id_count,
        }

    def validate_current(self) -> None:
        if type(self) is not MathNetSourceArtifactManifestV3:
            raise ValueError("MathNet source-manifest subclasses are forbidden")
        self._validate_fields()
        supplied = _validate_digest(self.content_digest, "MathNet source-manifest digest")
        expected = _digest(self._payload(), domain=_MANIFEST_DOMAIN)
        if not hmac.compare_digest(supplied, expected):
            raise ValueError("MathNet source-manifest digest is stale")

    def to_dict(self) -> dict[str, object]:
        self.validate_current()
        return self._payload() | {"content_digest": self.content_digest}


@dataclass(frozen=True, slots=True)
class MathNetAcquisitionRuntimeEvidenceV3:
    """Tool/runtime evidence kept separate from stable source-byte identity."""

    cache_mode: MathNetCacheModeV3
    remote_metadata_verified: bool
    immutable_snapshot_enforced: bool
    huggingface_hub_version: str
    pyarrow_version: str
    content_digest: str = field(init=False)

    def __post_init__(self) -> None:
        self._validate_fields()
        object.__setattr__(
            self,
            "content_digest",
            _digest(self._payload(), domain="org.math-research-brain/mathnet-runtime/v3"),
        )

    def _validate_fields(self) -> None:
        if type(self) is not MathNetAcquisitionRuntimeEvidenceV3:
            raise ValueError("MathNet runtime-evidence subclasses are forbidden")
        if type(self.cache_mode) is not MathNetCacheModeV3:
            raise ValueError("MathNet runtime evidence has an invalid cache mode")
        if type(self.remote_metadata_verified) is not bool:
            raise ValueError("MathNet remote-metadata evidence must be an exact boolean")
        if type(self.immutable_snapshot_enforced) is not bool:
            raise ValueError("MathNet immutable-snapshot evidence must be an exact boolean")
        if self.cache_mode is MathNetCacheModeV3.DOWNLOAD_MISSING and not (
            self.remote_metadata_verified
        ):
            raise ValueError("download mode requires live remote-metadata verification")
        for value, label in (
            (self.huggingface_hub_version, "huggingface_hub version"),
            (self.pyarrow_version, "PyArrow version"),
        ):
            if type(value) is not str or not value or len(value.encode("utf-8")) > 128:
                raise ValueError(f"MathNet {label} evidence is invalid")

    def _payload(self) -> dict[str, object]:
        return {
            "cache_mode": self.cache_mode.value,
            "huggingface_hub_version": self.huggingface_hub_version,
            "immutable_snapshot_enforced": self.immutable_snapshot_enforced,
            "pyarrow_version": self.pyarrow_version,
            "remote_metadata_verified": self.remote_metadata_verified,
        }

    def validate_current(self) -> None:
        if type(self) is not MathNetAcquisitionRuntimeEvidenceV3:
            raise ValueError("MathNet runtime-evidence subclasses are forbidden")
        self._validate_fields()
        supplied = _validate_digest(self.content_digest, "MathNet runtime-evidence digest")
        expected = _digest(
            self._payload(),
            domain="org.math-research-brain/mathnet-runtime/v3",
        )
        if not hmac.compare_digest(supplied, expected):
            raise ValueError("MathNet runtime-evidence digest is stale")

    def to_dict(self) -> dict[str, object]:
        self.validate_current()
        return self._payload() | {"content_digest": self.content_digest}


_PREPARED_SOURCE_TOKEN_V3 = object()


class PreparedPinnedMathNetSourceV3:
    """Process-local holder for audited paths; not itself proof of chronology.

    Python code under the same OS identity can inspect private attributes, so this class
    makes no secrecy or adversarial capability claim.  It does prevent accidental public
    dataclass serialization, pickling, and direct construction.  Official downstream code
    accepts only :class:`VerifiedPinnedMathNetBankV3`, which is derived after another byte
    check and complete row-stream replay.
    """

    __slots__ = (
        "_fingerprints",
        "_local_paths",
        "_manifest",
        "_runtime_evidence",
        "_token",
    )

    def __new__(cls, token: object = None) -> PreparedPinnedMathNetSourceV3:
        if token is not _PREPARED_SOURCE_TOKEN_V3:
            raise TypeError("prepared MathNet sources can only be created by acquisition")
        return object.__new__(cls)

    def __init__(self, token: object = None) -> None:
        del token
        raise TypeError("prepared MathNet sources can only be created by acquisition")

    @property
    def manifest(self) -> MathNetSourceArtifactManifestV3:
        return self._manifest

    @property
    def runtime_evidence(self) -> MathNetAcquisitionRuntimeEvidenceV3:
        return self._runtime_evidence

    def __repr__(self) -> str:
        return (
            "PreparedPinnedMathNetSourceV3("
            f"manifest_digest={self._manifest.content_digest!r}, shard_count=56)"
        )

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("prepared MathNet sources are deliberately non-pickleable")

    def __setattr__(self, name: str, value: object) -> None:
        del name, value
        raise AttributeError("prepared MathNet sources are immutable")

    @classmethod
    def _create(
        cls,
        *,
        manifest: MathNetSourceArtifactManifestV3,
        runtime_evidence: MathNetAcquisitionRuntimeEvidenceV3,
        local_paths: tuple[Path, ...],
        fingerprints: tuple[_LocalShardFingerprintV3, ...],
    ) -> PreparedPinnedMathNetSourceV3:
        instance = object.__new__(cls)
        object.__setattr__(instance, "_manifest", manifest)
        object.__setattr__(instance, "_runtime_evidence", runtime_evidence)
        object.__setattr__(instance, "_local_paths", local_paths)
        object.__setattr__(instance, "_fingerprints", fingerprints)
        object.__setattr__(instance, "_token", _PREPARED_SOURCE_TOKEN_V3)
        instance.validate_current()
        return instance

    def validate_current(self) -> None:
        if type(self) is not PreparedPinnedMathNetSourceV3 or self._token is not (
            _PREPARED_SOURCE_TOKEN_V3
        ):
            raise ValueError("MathNet prepared source was not produced by acquisition")
        if type(self._manifest) is not MathNetSourceArtifactManifestV3:
            raise ValueError("MathNet prepared source has no exact manifest")
        self._manifest.validate_current()
        if type(self._runtime_evidence) is not MathNetAcquisitionRuntimeEvidenceV3:
            raise ValueError("MathNet prepared source has no exact runtime evidence")
        self._runtime_evidence.validate_current()
        if type(self._local_paths) is not tuple or len(self._local_paths) != (
            MATHNET_SHARD_COUNT_V3
        ):
            raise ValueError("MathNet prepared source has the wrong local-file count")
        if type(self._fingerprints) is not tuple or len(self._fingerprints) != (
            MATHNET_SHARD_COUNT_V3
        ):
            raise ValueError("MathNet prepared source has the wrong fingerprint count")
        for path, fingerprint in zip(self._local_paths, self._fingerprints, strict=True):
            if not isinstance(path, Path) or not path.is_absolute():
                raise ValueError("MathNet prepared source paths must be absolute")
            if type(fingerprint) is not _LocalShardFingerprintV3:
                raise ValueError("MathNet prepared source fingerprint is not exact")


_VERIFIED_BANK_TOKEN_V3 = object()


class VerifiedPinnedMathNetBankV3:
    """Exact official-bank wrapper accepted by downstream study orchestration.

    A synthetic bank primitive is never this type.  Runtime evidence explicitly records
    whether an immutable same-handle snapshot was enforced, so engineering acquisition can
    be represented without silently passing the stronger confirmatory gate.
    """

    __slots__ = ("_bank", "_content_digest", "_manifest", "_runtime_evidence", "_token")

    def __new__(cls, token: object = None) -> VerifiedPinnedMathNetBankV3:
        if token is not _VERIFIED_BANK_TOKEN_V3:
            raise TypeError("verified MathNet banks can only be created by acquisition")
        return object.__new__(cls)

    def __init__(self, token: object = None) -> None:
        del token
        raise TypeError("verified MathNet banks can only be created by acquisition")

    @classmethod
    def _create(
        cls,
        *,
        manifest: MathNetSourceArtifactManifestV3,
        runtime_evidence: MathNetAcquisitionRuntimeEvidenceV3,
        bank: object,
    ) -> VerifiedPinnedMathNetBankV3:
        instance = object.__new__(cls)
        object.__setattr__(instance, "_manifest", manifest)
        object.__setattr__(instance, "_runtime_evidence", runtime_evidence)
        object.__setattr__(instance, "_bank", bank)
        object.__setattr__(instance, "_token", _VERIFIED_BANK_TOKEN_V3)
        object.__setattr__(
            instance,
            "_content_digest",
            _digest(instance._payload(), domain="org.math-research-brain/mathnet-official-bank/v3"),
        )
        instance.validate_current()
        return instance

    @property
    def manifest(self) -> MathNetSourceArtifactManifestV3:
        return self._manifest

    @property
    def runtime_evidence(self) -> MathNetAcquisitionRuntimeEvidenceV3:
        return self._runtime_evidence

    @property
    def bank(self) -> object:
        return self._bank

    @property
    def content_digest(self) -> str:
        return self._content_digest

    @property
    def confirmatory_source_gate(self) -> bool:
        self.validate_current()
        return bool(
            self._runtime_evidence.remote_metadata_verified
            and self._runtime_evidence.immutable_snapshot_enforced
        )

    def _payload(self) -> dict[str, object]:
        return {
            "bank_content_digest": getattr(self._bank, "content_digest", None),
            "manifest_content_digest": self._manifest.content_digest,
            "runtime_evidence_digest": self._runtime_evidence.content_digest,
            "source_authority": "official_byte_verified",
        }

    def validate_current(self) -> None:
        if type(self) is not VerifiedPinnedMathNetBankV3 or self._token is not (
            _VERIFIED_BANK_TOKEN_V3
        ):
            raise ValueError("official MathNet wrapper is not exact")
        if type(self._manifest) is not MathNetSourceArtifactManifestV3:
            raise ValueError("official MathNet wrapper has no exact source manifest")
        self._manifest.validate_current()
        if type(self._runtime_evidence) is not MathNetAcquisitionRuntimeEvidenceV3:
            raise ValueError("official MathNet wrapper has no exact runtime evidence")
        self._runtime_evidence.validate_current()

        from .mathnet_bank_v3 import (
            MathNetBankV3,
            MathNetSourceAuthorityV3,
        )

        if type(self._bank) is not MathNetBankV3:
            raise ValueError("official MathNet wrapper has no exact private bank")
        self._bank.validate_current()
        if self._bank.source_binding.authority is not (
            MathNetSourceAuthorityV3.OFFICIAL_BYTE_VERIFIED
        ):
            raise ValueError("synthetic MathNet bank cannot enter the official wrapper")
        if self._bank.source_binding.source_artifact_manifest_digest != (
            self._manifest.content_digest
        ):
            raise ValueError("official MathNet bank and artifact manifest differ")
        supplied = _validate_digest(self._content_digest, "official MathNet wrapper digest")
        expected = _digest(
            self._payload(),
            domain="org.math-research-brain/mathnet-official-bank/v3",
        )
        if not hmac.compare_digest(supplied, expected):
            raise ValueError("official MathNet wrapper digest is stale")

    def public_commitment_payload(self) -> dict[str, object]:
        self.validate_current()
        return self._payload() | {
            "confirmatory_source_gate": self.confirmatory_source_gate,
            "content_digest": self._content_digest,
        }

    def __repr__(self) -> str:
        return (
            "VerifiedPinnedMathNetBankV3("
            f"content_digest={self._content_digest!r}, "
            f"confirmatory_source_gate={self.confirmatory_source_gate!r})"
        )

    def __setattr__(self, name: str, value: object) -> None:
        del name, value
        raise AttributeError("verified MathNet banks are immutable")


_PREPARED_PILOT_UNIVERSE_TOKEN_V3 = object()
_PILOT_PREPARED_CREATE_TOKEN_V3 = object()


class PreparedMathNetPilotUniverseV3:
    """Process-local gold-free preparation result for the source-pinned pilot.

    This wrapper deliberately offers no public prompt-row or bare-universe accessor.  Private
    attributes remain reachable to same-process Python and are not a security boundary.  A
    later blinded audit-preparation layer must consume it and emit a narrower signed
    projection before any classifier or selector process runs.  The immutable-snapshot gap is
    permanent for protocol v3, so this result can never pass a confirmatory gate.
    """

    __slots__ = (
        "_content_digest",
        "_manifest",
        "_runtime_evidence",
        "_token",
        "_universe",
    )

    def __new__(
        cls,
        token: object = None,
    ) -> PreparedMathNetPilotUniverseV3:
        if token is not _PREPARED_PILOT_UNIVERSE_TOKEN_V3:
            raise TypeError("prepared MathNet pilot universes require acquisition replay")
        return object.__new__(cls)

    def __init__(self, token: object = None) -> None:
        del token
        raise TypeError("prepared MathNet pilot universes require acquisition replay")

    @classmethod
    def _create(
        cls,
        *,
        manifest: MathNetSourceArtifactManifestV3,
        runtime_evidence: MathNetAcquisitionRuntimeEvidenceV3,
        universe: object,
        creation_guard: object,
    ) -> PreparedMathNetPilotUniverseV3:
        if creation_guard is not _PILOT_PREPARED_CREATE_TOKEN_V3:
            raise ValueError("prepared pilot universe requires the acquisition replay token")
        instance = object.__new__(cls)
        object.__setattr__(instance, "_manifest", manifest)
        object.__setattr__(instance, "_runtime_evidence", runtime_evidence)
        object.__setattr__(instance, "_universe", universe)
        object.__setattr__(instance, "_token", _PREPARED_PILOT_UNIVERSE_TOKEN_V3)
        object.__setattr__(
            instance,
            "_content_digest",
            _digest(instance._payload(), domain=_OFFICIAL_PILOT_UNIVERSE_DOMAIN),
        )
        instance.validate_current()
        return instance

    @property
    def manifest(self) -> MathNetSourceArtifactManifestV3:
        return self._manifest

    @property
    def runtime_evidence(self) -> MathNetAcquisitionRuntimeEvidenceV3:
        return self._runtime_evidence

    @property
    def content_digest(self) -> str:
        return self._content_digest

    @property
    def universe_content_digest(self) -> str:
        self.validate_current()
        return self._universe.content_digest

    @property
    def screen_counts(self) -> tuple[tuple[str, int], ...]:
        self.validate_current()
        return self._universe.screen_counts

    @property
    def candidate_count(self) -> int:
        self.validate_current()
        return len(self._universe.rows)

    @property
    def confirmatory_source_gate(self) -> bool:
        self.validate_current()
        return False

    def _payload(self) -> dict[str, object]:
        return {
            "audited_projection_handoff_authorized": False,
            "confirmatory_source_gate": False,
            "engineering_pilot_only": True,
            "external_signature_attested": False,
            "gold_free_universe": self._universe.public_commitment_payload(),
            "in_process_creation_identity_attested": False,
            "local_source_replay_required_for_use": True,
            "manifest_content_digest": self._manifest.content_digest,
            "preparation_code_identity_attested": False,
            "runtime_evidence_digest": self._runtime_evidence.content_digest,
            "selector_handoff_authorized": False,
            "source_binding_claim": "official_byte_verified",
            "wrapper_self_authenticating": False,
        }

    def validate_current(self) -> None:
        if type(self) is not PreparedMathNetPilotUniverseV3 or self._token is not (
            _PREPARED_PILOT_UNIVERSE_TOKEN_V3
        ):
            raise ValueError("prepared MathNet pilot-universe wrapper is not exact")
        if type(self._manifest) is not MathNetSourceArtifactManifestV3:
            raise ValueError("prepared pilot universe has no exact source manifest")
        self._manifest.validate_current()
        if type(self._runtime_evidence) is not MathNetAcquisitionRuntimeEvidenceV3:
            raise ValueError("prepared pilot universe has no exact runtime evidence")
        self._runtime_evidence.validate_current()

        from .mathnet_bank_v3 import MathNetSourceAuthorityV3
        from .mathnet_pilot_material_v3 import GoldFreeMathNetPilotUniverseV3

        if type(self._universe) is not GoldFreeMathNetPilotUniverseV3:
            raise ValueError("prepared pilot-universe wrapper has no exact universe")
        self._universe.validate_current()
        if self._universe.source_binding.authority is not (
            MathNetSourceAuthorityV3.OFFICIAL_BYTE_VERIFIED
        ):
            raise ValueError("synthetic pilot universe cannot enter the official wrapper")
        if self._universe.source_binding.source_artifact_manifest_digest != (
            self._manifest.content_digest
        ):
            raise ValueError("prepared pilot universe and source manifest differ")
        supplied = _validate_digest(
            self._content_digest,
            "prepared MathNet pilot-universe wrapper digest",
        )
        expected = _digest(self._payload(), domain=_OFFICIAL_PILOT_UNIVERSE_DOMAIN)
        if not hmac.compare_digest(supplied, expected):
            raise ValueError("prepared MathNet pilot-universe wrapper digest is stale")

    def public_commitment_payload(self) -> dict[str, object]:
        self.validate_current()
        return self._payload() | {"content_digest": self._content_digest}

    def __repr__(self) -> str:
        return (
            "PreparedMathNetPilotUniverseV3("
            f"content_digest={self._content_digest!r}, "
            f"candidate_count={self.candidate_count!r}, "
            "confirmatory_source_gate=False)"
        )

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("prepared MathNet pilot universes are deliberately non-pickleable")

    def __setattr__(self, name: str, value: object) -> None:
        del name, value
        raise AttributeError("prepared MathNet pilot universes are immutable")


_PREPARED_PILOT_EVALUATOR_VAULT_TOKEN_V3 = object()


class PreparedMathNetPilotEvaluatorVaultV3:
    """Evaluator-only keyed vault with no public gold accessor or secrecy claim."""

    __slots__ = (
        "_bound_universe_content_digest",
        "_bound_universe_wrapper_digest",
        "_content_digest",
        "_manifest",
        "_runtime_evidence",
        "_token",
        "_vault",
    )

    def __new__(
        cls,
        token: object = None,
    ) -> PreparedMathNetPilotEvaluatorVaultV3:
        if token is not _PREPARED_PILOT_EVALUATOR_VAULT_TOKEN_V3:
            raise TypeError("prepared MathNet pilot vaults require acquisition replay")
        return object.__new__(cls)

    def __init__(self, token: object = None) -> None:
        del token
        raise TypeError("prepared MathNet pilot vaults require acquisition replay")

    @classmethod
    def _create(
        cls,
        *,
        manifest: MathNetSourceArtifactManifestV3,
        runtime_evidence: MathNetAcquisitionRuntimeEvidenceV3,
        vault: object,
        bound_universe: PreparedMathNetPilotUniverseV3,
        creation_guard: object,
    ) -> PreparedMathNetPilotEvaluatorVaultV3:
        if creation_guard is not _PILOT_PREPARED_CREATE_TOKEN_V3:
            raise ValueError("prepared pilot vault requires the acquisition replay token")
        if type(bound_universe) is not PreparedMathNetPilotUniverseV3:
            raise ValueError("prepared pilot vault requires an exact universe wrapper")
        bound_universe.validate_current()
        instance = object.__new__(cls)
        object.__setattr__(instance, "_manifest", manifest)
        object.__setattr__(instance, "_runtime_evidence", runtime_evidence)
        object.__setattr__(instance, "_vault", vault)
        object.__setattr__(
            instance,
            "_bound_universe_content_digest",
            bound_universe.universe_content_digest,
        )
        object.__setattr__(
            instance,
            "_bound_universe_wrapper_digest",
            bound_universe.content_digest,
        )
        object.__setattr__(
            instance,
            "_token",
            _PREPARED_PILOT_EVALUATOR_VAULT_TOKEN_V3,
        )
        object.__setattr__(
            instance,
            "_content_digest",
            _digest(
                instance._payload(),
                domain=_OFFICIAL_PILOT_EVALUATOR_VAULT_DOMAIN,
            ),
        )
        instance.validate_current()
        return instance

    @property
    def manifest(self) -> MathNetSourceArtifactManifestV3:
        return self._manifest

    @property
    def runtime_evidence(self) -> MathNetAcquisitionRuntimeEvidenceV3:
        return self._runtime_evidence

    @property
    def content_digest(self) -> str:
        return self._content_digest

    @property
    def bound_universe_content_digest(self) -> str:
        self.validate_current()
        return self._bound_universe_content_digest

    @property
    def bound_universe_wrapper_digest(self) -> str:
        self.validate_current()
        return self._bound_universe_wrapper_digest

    @property
    def entry_count(self) -> int:
        self.validate_current()
        return len(self._vault.entries)

    @property
    def confirmatory_source_gate(self) -> bool:
        self.validate_current()
        return False

    def _payload(self) -> dict[str, object]:
        return {
            "bound_universe_content_digest": self._bound_universe_content_digest,
            "bound_universe_wrapper_digest": self._bound_universe_wrapper_digest,
            "confirmatory_source_gate": False,
            "engineering_pilot_only": True,
            "evaluator_only": True,
            "external_signature_attested": False,
            "in_process_creation_identity_attested": False,
            "local_source_replay_required_for_use": True,
            "manifest_content_digest": self._manifest.content_digest,
            "preparation_code_identity_attested": False,
            "runtime_evidence_digest": self._runtime_evidence.content_digest,
            "selector_handoff_authorized": False,
            "source_binding_claim": "official_byte_verified",
            "vault_public_commitment": self._vault.public_commitment_payload(),
            "wrapper_self_authenticating": False,
        }

    def validate_current(self) -> None:
        if (
            type(self) is not PreparedMathNetPilotEvaluatorVaultV3
            or self._token is not _PREPARED_PILOT_EVALUATOR_VAULT_TOKEN_V3
        ):
            raise ValueError("prepared MathNet pilot-vault wrapper is not exact")
        if type(self._manifest) is not MathNetSourceArtifactManifestV3:
            raise ValueError("prepared pilot vault has no exact source manifest")
        self._manifest.validate_current()
        if type(self._runtime_evidence) is not MathNetAcquisitionRuntimeEvidenceV3:
            raise ValueError("prepared pilot vault has no exact runtime evidence")
        self._runtime_evidence.validate_current()

        from .mathnet_bank_v3 import _official_mathnet_source_binding_v3
        from .mathnet_pilot_material_v3 import MathNetPilotGoldVaultV3

        if type(self._vault) is not MathNetPilotGoldVaultV3:
            raise ValueError("prepared pilot-vault wrapper has no exact keyed vault")
        self._vault.validate_current()
        expected_binding = _official_mathnet_source_binding_v3(
            self._manifest.content_digest
        )
        if self._vault.source_binding_digest != expected_binding.content_digest:
            raise ValueError("prepared pilot vault and source manifest differ")
        _validate_digest(
            self._bound_universe_content_digest,
            "prepared pilot vault-bound universe",
        )
        _validate_digest(
            self._bound_universe_wrapper_digest,
            "prepared pilot vault-bound wrapper",
        )
        if not hmac.compare_digest(
            self._vault.bound_universe_content_digest,
            self._bound_universe_content_digest,
        ):
            raise ValueError("prepared pilot vault has a stale universe binding")
        supplied = _validate_digest(
            self._content_digest,
            "prepared MathNet pilot-vault wrapper digest",
        )
        expected = _digest(
            self._payload(),
            domain=_OFFICIAL_PILOT_EVALUATOR_VAULT_DOMAIN,
        )
        if not hmac.compare_digest(supplied, expected):
            raise ValueError("prepared MathNet pilot-vault wrapper digest is stale")

    def verify_key(self, vault_key: bytes) -> None:
        self.validate_current()
        self._vault.verify_key(vault_key)

    def public_commitment_payload(self) -> dict[str, object]:
        self.validate_current()
        return self._payload() | {"content_digest": self._content_digest}

    def __repr__(self) -> str:
        return (
            "PreparedMathNetPilotEvaluatorVaultV3("
            f"content_digest={self._content_digest!r}, "
            f"entry_count={self.entry_count!r}, evaluator_only=True, "
            "confirmatory_source_gate=False)"
        )

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("prepared MathNet pilot vaults are deliberately non-pickleable")

    def __setattr__(self, name: str, value: object) -> None:
        del name, value
        raise AttributeError("prepared MathNet pilot vaults are immutable")


def _prepared_pilot_universe_value_v3(
    prepared: PreparedMathNetPilotUniverseV3,
) -> object:
    if type(prepared) is not PreparedMathNetPilotUniverseV3:
        raise ValueError("pilot universe access requires the exact prepared wrapper")
    prepared.validate_current()
    return prepared._universe


def _prepared_pilot_vault_value_v3(
    prepared: PreparedMathNetPilotEvaluatorVaultV3,
) -> object:
    if type(prepared) is not PreparedMathNetPilotEvaluatorVaultV3:
        raise ValueError("pilot vault access requires the exact evaluator wrapper")
    prepared.validate_current()
    return prepared._vault


def _hash_local_shard_v3(path: Path, expected: ExpectedShardV3) -> _LocalShardFingerprintV3:
    if not isinstance(path, Path) or not path.is_absolute():
        raise ValueError("MathNet local shard path must be an absolute Path")
    try:
        resolved = path.resolve(strict=True)
        before = resolved.stat()
    except OSError as exc:
        raise ValueError(f"MathNet shard {expected.ordinal} is not readable") from exc
    if not resolved.is_file() or before.st_size != expected.size_bytes:
        raise ValueError(f"MathNet shard {expected.ordinal} has the wrong local size")

    digest = hashlib.sha256()
    first = b""
    tail = b""
    try:
        with resolved.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                if not first:
                    first = chunk[:4]
                tail = (tail + chunk)[-4:]
                digest.update(chunk)
        after = resolved.stat()
    except OSError as exc:
        raise ValueError(f"MathNet shard {expected.ordinal} changed while hashing") from exc
    before_tuple = (before.st_size, before.st_mtime_ns, before.st_dev, before.st_ino)
    after_tuple = (after.st_size, after.st_mtime_ns, after.st_dev, after.st_ino)
    if before_tuple != after_tuple:
        raise ValueError(f"MathNet shard {expected.ordinal} changed while hashing")
    if first != b"PAR1" or tail != b"PAR1":
        raise ValueError(f"MathNet shard {expected.ordinal} is not a complete Parquet file")
    observed = digest.hexdigest()
    if not hmac.compare_digest(observed, expected.lfs_sha256):
        raise ValueError(f"MathNet shard {expected.ordinal} bytes differ from the LFS pin")
    return _LocalShardFingerprintV3(
        size_bytes=after.st_size,
        modified_ns=after.st_mtime_ns,
        device=after.st_dev,
        inode=after.st_ino,
        sha256=observed,
    )


def _arrow_type_descriptor_v3(data_type: object, pa: Any) -> dict[str, object]:
    if pa.types.is_string(data_type):
        return {"kind": "string"}
    if pa.types.is_binary(data_type):
        return {"kind": "binary"}
    if pa.types.is_list(data_type):
        return {
            "kind": "list",
            "value": _arrow_field_descriptor_v3(data_type.value_field, pa),
        }
    if pa.types.is_struct(data_type):
        return {
            "fields": tuple(_arrow_field_descriptor_v3(field, pa) for field in data_type),
            "kind": "struct",
        }
    raise ValueError("MathNet Arrow schema contains an unsupported data type")


def _arrow_field_descriptor_v3(field: object, pa: Any) -> dict[str, object]:
    name = getattr(field, "name", None)
    nullable = getattr(field, "nullable", None)
    data_type = getattr(field, "type", None)
    if type(name) is not str or type(nullable) is not bool or data_type is None:
        raise ValueError("MathNet Arrow field descriptor is invalid")
    return {
        "name": name,
        "nullable": nullable,
        "type": _arrow_type_descriptor_v3(data_type, pa),
    }


def _audit_parquet_footer_v3(
    path: Path,
    expected: ExpectedShardV3,
    *,
    pa: Any,
    pq: Any,
) -> FooterAuditV3:
    parquet = pq.ParquetFile(
        path,
        memory_map=False,
        pre_buffer=False,
        page_checksum_verification=True,
        arrow_extensions_enabled=False,
        thrift_string_size_limit=8 * 1024 * 1024,
        thrift_container_size_limit=1_000_000,
    )
    metadata = parquet.metadata
    if metadata is None:
        raise ValueError(f"MathNet shard {expected.ordinal} has no Parquet metadata")
    physical = tuple(
        PhysicalLeafV3(
            path=parquet.schema.column(index).path,
            physical_type=parquet.schema.column(index).physical_type,
            logical_type=str(parquet.schema.column(index).logical_type),
            max_definition_level=parquet.schema.column(index).max_definition_level,
            max_repetition_level=parquet.schema.column(index).max_repetition_level,
        )
        for index in range(len(parquet.schema))
    )
    arrow_descriptor = tuple(
        _arrow_field_descriptor_v3(field, pa) for field in parquet.schema_arrow
    )
    arrow_digest = _digest(arrow_descriptor, domain=_ARROW_SCHEMA_DOMAIN)
    return FooterAuditV3(
        ordinal=expected.ordinal,
        row_count=metadata.num_rows,
        row_group_count=metadata.num_row_groups,
        physical_leaves=physical,
        arrow_schema_digest=arrow_digest,
        created_by=metadata.created_by,
    )


def _open_parquet_v3(path: Path, pq: Any) -> object:
    return pq.ParquetFile(
        path,
        memory_map=False,
        pre_buffer=False,
        page_checksum_verification=True,
        arrow_extensions_enabled=False,
        thrift_string_size_limit=8 * 1024 * 1024,
        thrift_container_size_limit=1_000_000,
    )


def _audit_unique_ids_v3(local_paths: tuple[Path, ...], pq: Any) -> int:
    ids: set[str] = set()
    observed_rows = 0
    for ordinal, path in enumerate(local_paths):
        shard_rows = 0
        parquet = _open_parquet_v3(path, pq)
        for batch in parquet.iter_batches(
            batch_size=4096,
            columns=("id",),
            use_threads=False,
        ):
            if tuple(batch.schema.names) != ("id",):
                raise ValueError("MathNet ID projection returned an unexpected schema")
            for raw in batch.to_pylist():
                if type(raw) is not dict or tuple(raw) != ("id",):
                    raise ValueError("MathNet ID projection returned an invalid row")
                source_id = raw["id"]
                if type(source_id) is not str or not source_id:
                    raise ValueError(f"MathNet shard {ordinal} contains a null or invalid ID")
                if source_id in ids:
                    raise ValueError(f"MathNet shard {ordinal} contains a duplicate ID")
                ids.add(source_id)
                shard_rows += 1
        if shard_rows != MATHNET_SHARD_ROWS_V3[ordinal]:
            raise ValueError(f"MathNet shard {ordinal} yielded the wrong ID row count")
        observed_rows += shard_rows
    if observed_rows != MATHNET_TOTAL_ROWS_V3 or len(ids) != MATHNET_TOTAL_ROWS_V3:
        raise ValueError("MathNet source ID audit did not cover the pinned row universe")
    return len(ids)


def _dependency_version_v3(module: object, label: str) -> str:
    value = getattr(module, "__version__", None)
    if type(value) is not str or not value or len(value.encode("utf-8")) > 128:
        raise ValueError(f"{label} does not expose a bounded exact version")
    return value


def prepare_pinned_mathnet_source_v3(
    *,
    cache_mode: MathNetCacheModeV3 = MathNetCacheModeV3.REQUIRE_PRESENT,
    cache_dir: Path | None = None,
) -> PreparedPinnedMathNetSourceV3:
    """Verify the exact official pin and return a non-serializable source capability.

    This performs 56 complete SHA-256 passes plus Parquet schema and unique-ID audits.
    Callers must size this work under the machine compute governor.  The default never
    downloads missing artifacts.
    """

    if type(cache_mode) is not MathNetCacheModeV3:
        raise ValueError("MathNet cache mode must be exact")
    if cache_dir is not None:
        if not isinstance(cache_dir, Path) or not cache_dir.is_absolute():
            raise ValueError("MathNet cache directory must be an absolute Path or null")
        cache_dir = cache_dir.resolve(strict=False)

    try:
        import huggingface_hub
        import pyarrow as pa
        import pyarrow.parquet as pq
        from huggingface_hub import HfApi, hf_hub_download
    except ImportError as exc:  # pragma: no cover - exercised without optional extra
        raise RuntimeError(
            "MathNet acquisition requires the mathnet-acquisition optional dependencies"
        ) from exc

    remote_metadata_verified = cache_mode is MathNetCacheModeV3.DOWNLOAD_MISSING
    if remote_metadata_verified:
        api = HfApi(endpoint="https://huggingface.co", token=False)
        info = api.dataset_info(
            MATHNET_REPOSITORY_V3,
            revision=MATHNET_REVISION_V3,
            files_metadata=True,
            token=False,
        )
        verify_mathnet_remote_metadata_v3(info)

    paths: list[Path] = []
    fingerprints: list[_LocalShardFingerprintV3] = []
    footers: list[FooterAuditV3] = []
    for expected in EXPECTED_MATHNET_SHARDS_V3:
        materialized = hf_hub_download(
            repo_id=MATHNET_REPOSITORY_V3,
            filename=expected.path,
            repo_type="dataset",
            revision=MATHNET_REVISION_V3,
            cache_dir=cache_dir,
            local_files_only=(cache_mode is MathNetCacheModeV3.REQUIRE_PRESENT),
            token=False,
            endpoint="https://huggingface.co",
        )
        if type(materialized) is not str:
            raise ValueError("MathNet downloader did not return one exact local path")
        path = Path(materialized).resolve(strict=True)
        fingerprint = _hash_local_shard_v3(path, expected)
        footer = _audit_parquet_footer_v3(path, expected, pa=pa, pq=pq)
        paths.append(path)
        fingerprints.append(fingerprint)
        footers.append(footer)

    path_tuple = tuple(paths)
    fingerprint_tuple = tuple(fingerprints)
    manifest = MathNetSourceArtifactManifestV3(
        repository_id=MATHNET_REPOSITORY_V3,
        revision=MATHNET_REVISION_V3,
        config_name=MATHNET_CONFIG_V3,
        split=MATHNET_SPLIT_V3,
        shards=EXPECTED_MATHNET_SHARDS_V3,
        footer_audits=tuple(footers),
        total_bytes=sum(item.size_bytes for item in fingerprints),
        total_rows=sum(item.row_count for item in footers),
        unique_id_count=_audit_unique_ids_v3(path_tuple, pq),
        arrow_schema_digest=MATHNET_ARROW_SCHEMA_DIGEST_V3,
        row_adapter_digest=MATHNET_ROW_ADAPTER_DIGEST_V3,
    )
    runtime_evidence = MathNetAcquisitionRuntimeEvidenceV3(
        cache_mode=cache_mode,
        remote_metadata_verified=remote_metadata_verified,
        immutable_snapshot_enforced=False,
        huggingface_hub_version=_dependency_version_v3(
            huggingface_hub,
            "huggingface_hub",
        ),
        pyarrow_version=_dependency_version_v3(pa, "PyArrow"),
    )
    return PreparedPinnedMathNetSourceV3._create(
        manifest=manifest,
        runtime_evidence=runtime_evidence,
        local_paths=path_tuple,
        fingerprints=fingerprint_tuple,
    )


def _verify_prepared_bytes_v3(source: PreparedPinnedMathNetSourceV3) -> None:
    source.validate_current()
    for expected, path, frozen in zip(
        EXPECTED_MATHNET_SHARDS_V3,
        source._local_paths,
        source._fingerprints,
        strict=True,
    ):
        observed = _hash_local_shard_v3(path, expected)
        if observed != frozen:
            raise ValueError(f"MathNet shard {expected.ordinal} changed after preparation")


def _validated_batch_size_v3(batch_size: object) -> int:
    if type(batch_size) is not int or not 1 <= batch_size <= 65_536:
        raise ValueError("MathNet batch size must be an exact integer in [1, 65536]")
    return batch_size


def _verify_expected_pinned_pilot_universe_v3(universe: Any) -> None:
    """Reject silent v3 screening or membership drift for the exact pinned source."""

    if universe.screen_counts != EXPECTED_MATHNET_PILOT_SCREEN_COUNTS_V3:
        raise ValueError("MathNet pilot screen counts differ from the pinned v3 universe")
    if len(universe.rows) != EXPECTED_MATHNET_PILOT_CANDIDATE_COUNT_V3:
        raise ValueError("MathNet pilot candidate count differs from the pinned v3 universe")
    public = universe.public_commitment_payload()
    for key, expected in EXPECTED_MATHNET_PILOT_UNIVERSE_COMMITMENTS_V3:
        observed = _validate_digest(public.get(key), f"MathNet pilot {key}")
        if not hmac.compare_digest(observed, expected):
            raise ValueError(f"MathNet pilot {key} differs from the pinned v3 universe")


def _iter_prepared_rows_v3(
    source: PreparedPinnedMathNetSourceV3,
    *,
    batch_size: int,
) -> Iterator[dict[str, object]]:
    batch_size = _validated_batch_size_v3(batch_size)
    try:
        import pyarrow.parquet as pq
    except ImportError as exc:  # pragma: no cover - exercised without optional extra
        raise RuntimeError("MathNet row streaming requires PyArrow") from exc

    expected_names = (
        "id",
        "problem_markdown",
        "solutions_markdown",
        "images",
        "country",
        "competition",
        "topics_flat",
        "language",
        "problem_type",
        "final_answer",
    )
    total_rows = 0
    for ordinal, path in enumerate(source._local_paths):
        parquet = _open_parquet_v3(path, pq)
        shard_rows = 0
        for batch in parquet.iter_batches(
            batch_size=batch_size,
            columns=MATHNET_ROW_PROJECTION_V3,
            use_threads=False,
        ):
            if tuple(batch.schema.names) != expected_names:
                raise ValueError("MathNet row projection returned an unexpected schema")
            for raw in batch.to_pylist():
                if type(raw) is not dict or tuple(raw) != expected_names:
                    raise ValueError("MathNet projection returned a noncanonical row")
                images = raw["images"]
                if type(images) is not list:
                    raise ValueError(f"MathNet shard {ordinal} has a null images list")
                for image in images:
                    if type(image) is not dict or tuple(image) != ("path",) or type(
                        image["path"]
                    ) not in (str, type(None)):
                        raise ValueError("MathNet image-path projection is not path-only")
                yield {
                    "id": raw["id"],
                    "problem_markdown": raw["problem_markdown"],
                    "solutions_markdown": raw["solutions_markdown"],
                    "images": [None] * len(images),
                    "country": raw["country"],
                    "competition": raw["competition"],
                    "topics_flat": raw["topics_flat"],
                    "language": raw["language"],
                    "problem_type": raw["problem_type"],
                    "final_answer": raw["final_answer"],
                }
                shard_rows += 1
        if shard_rows != MATHNET_SHARD_ROWS_V3[ordinal]:
            raise ValueError(f"MathNet shard {ordinal} yielded the wrong row count")
        total_rows += shard_rows
    if total_rows != MATHNET_TOTAL_ROWS_V3:
        raise ValueError("MathNet prepared source yielded the wrong total row count")


def build_pinned_mathnet_bank_v3(
    source: PreparedPinnedMathNetSourceV3,
    *,
    sample_size: int,
    selection_seed: bytes,
    batch_size: int = 512,
) -> VerifiedPinnedMathNetBankV3:
    """Build the private bank only from a byte-verified prepared capability.

    The concrete return bank is intentionally imported lazily so importing this acquisition
    module does not pull its private study records into metadata-only checks.
    """

    if type(source) is not PreparedPinnedMathNetSourceV3:
        raise ValueError("official MathNet bank construction requires a prepared source")
    source.validate_current()
    _verify_prepared_bytes_v3(source)

    from .mathnet_bank_v3 import (
        _build_mathnet_bank_v3,
        _official_mathnet_source_binding_v3,
    )

    binding = _official_mathnet_source_binding_v3(source.manifest.content_digest)
    bank = _build_mathnet_bank_v3(
        _iter_prepared_rows_v3(source, batch_size=batch_size),
        source_binding=binding,
        sample_size=sample_size,
        selection_seed=selection_seed,
    )
    _verify_prepared_bytes_v3(source)
    return VerifiedPinnedMathNetBankV3._create(
        manifest=source.manifest,
        runtime_evidence=source.runtime_evidence,
        bank=bank,
    )


def verify_pinned_mathnet_bank_v3(
    source: PreparedPinnedMathNetSourceV3,
    verified_bank: VerifiedPinnedMathNetBankV3,
    *,
    selection_seed: bytes,
    batch_size: int = 512,
) -> None:
    """Replay an official wrapper from its prepared byte source and revealed seed."""

    if type(verified_bank) is not VerifiedPinnedMathNetBankV3:
        raise ValueError("official MathNet replay rejects a synthetic or bare bank")
    if type(source) is not PreparedPinnedMathNetSourceV3:
        raise ValueError("official MathNet replay requires a prepared source")
    source.validate_current()
    verified_bank.validate_current()
    if verified_bank.manifest != source.manifest:
        raise ValueError("official MathNet replay source differs from the bank manifest")
    _verify_prepared_bytes_v3(source)

    from .mathnet_bank_v3 import (
        _official_mathnet_source_binding_v3,
        _verify_mathnet_bank_v3,
    )

    binding = _official_mathnet_source_binding_v3(source.manifest.content_digest)
    _verify_mathnet_bank_v3(
        verified_bank.bank,
        _iter_prepared_rows_v3(source, batch_size=batch_size),
        source_binding=binding,
        selection_seed=selection_seed,
    )
    _verify_prepared_bytes_v3(source)


def build_pinned_mathnet_pilot_artifacts_v3(
    source: PreparedPinnedMathNetSourceV3,
    *,
    vault_key: bytes,
    batch_size: int = 512,
) -> tuple[
    PreparedMathNetPilotUniverseV3,
    PreparedMathNetPilotEvaluatorVaultV3,
]:
    """Build separate source-pinned process-local results in one exact source pass.

    The combined material object exists only on this trusted preparation stack.  It is not
    retained by either return value and cannot be handed to a classifier or selector.
    """

    if type(source) is not PreparedPinnedMathNetSourceV3:
        raise ValueError("source-pinned MathNet pilot construction requires a prepared source")
    source.validate_current()
    batch_size = _validated_batch_size_v3(batch_size)

    from .mathnet_bank_v3 import _official_mathnet_source_binding_v3
    from .mathnet_pilot_material_v3 import (
        _build_mathnet_pilot_material_v3,
        _validate_vault_key,
    )

    key = _validate_vault_key(vault_key)
    _verify_prepared_bytes_v3(source)
    binding = _official_mathnet_source_binding_v3(source.manifest.content_digest)
    material = _build_mathnet_pilot_material_v3(
        _iter_prepared_rows_v3(source, batch_size=batch_size),
        source_binding=binding,
        vault_key=key,
    )
    _verify_expected_pinned_pilot_universe_v3(material.universe)
    _verify_prepared_bytes_v3(source)
    prepared_universe = PreparedMathNetPilotUniverseV3._create(
        manifest=source.manifest,
        runtime_evidence=source.runtime_evidence,
        universe=material.universe,
        creation_guard=_PILOT_PREPARED_CREATE_TOKEN_V3,
    )
    evaluator_vault = PreparedMathNetPilotEvaluatorVaultV3._create(
        manifest=source.manifest,
        runtime_evidence=source.runtime_evidence,
        vault=material.vault,
        bound_universe=prepared_universe,
        creation_guard=_PILOT_PREPARED_CREATE_TOKEN_V3,
    )
    return prepared_universe, evaluator_vault


def verify_pinned_mathnet_pilot_artifacts_v3(
    source: PreparedPinnedMathNetSourceV3,
    prepared_universe: PreparedMathNetPilotUniverseV3,
    evaluator_vault: PreparedMathNetPilotEvaluatorVaultV3,
    *,
    vault_key: bytes,
    batch_size: int = 512,
) -> None:
    """Replay both prepared pilot results against pinned bytes and the vault key."""

    if type(source) is not PreparedPinnedMathNetSourceV3:
        raise ValueError("source-pinned MathNet pilot replay requires a prepared source")
    if type(prepared_universe) is not PreparedMathNetPilotUniverseV3:
        raise ValueError("source-pinned MathNet pilot replay rejects a bare universe")
    if type(evaluator_vault) is not PreparedMathNetPilotEvaluatorVaultV3:
        raise ValueError("source-pinned MathNet pilot replay rejects a bare or synthetic vault")
    source.validate_current()
    prepared_universe.validate_current()
    evaluator_vault.validate_current()
    batch_size = _validated_batch_size_v3(batch_size)
    evaluator_vault.verify_key(vault_key)
    if prepared_universe.manifest != source.manifest or evaluator_vault.manifest != (
        source.manifest
    ):
        raise ValueError("source-pinned MathNet pilot replay source manifest differs")
    if prepared_universe.runtime_evidence != source.runtime_evidence or (
        evaluator_vault.runtime_evidence != source.runtime_evidence
    ):
        raise ValueError("source-pinned MathNet pilot replay runtime evidence differs")
    if evaluator_vault.bound_universe_wrapper_digest != prepared_universe.content_digest:
        raise ValueError("source-pinned MathNet pilot wrapper-pair binding differs")
    if evaluator_vault.bound_universe_content_digest != (
        prepared_universe.universe_content_digest
    ):
        raise ValueError("source-pinned MathNet pilot universe binding differs")

    from .mathnet_bank_v3 import _official_mathnet_source_binding_v3
    from .mathnet_pilot_material_v3 import (
        MathNetPilotMaterialV3,
        _verify_mathnet_pilot_material_v3,
    )

    universe = _prepared_pilot_universe_value_v3(prepared_universe)
    vault = _prepared_pilot_vault_value_v3(evaluator_vault)
    _verify_expected_pinned_pilot_universe_v3(universe)
    material = MathNetPilotMaterialV3._derive(universe=universe, vault=vault)
    _verify_prepared_bytes_v3(source)
    binding = _official_mathnet_source_binding_v3(source.manifest.content_digest)
    _verify_mathnet_pilot_material_v3(
        material,
        _iter_prepared_rows_v3(source, batch_size=batch_size),
        source_binding=binding,
        vault_key=vault_key,
    )
    _verify_prepared_bytes_v3(source)


if len(EXPECTED_MATHNET_SHARDS_V3) != MATHNET_SHARD_COUNT_V3:
    raise RuntimeError("MathNet shard pin count is internally inconsistent")
if sum(item.size_bytes for item in EXPECTED_MATHNET_SHARDS_V3) != MATHNET_TOTAL_BYTES_V3:
    raise RuntimeError("MathNet shard byte pins are internally inconsistent")
if sum(item.row_count for item in EXPECTED_MATHNET_SHARDS_V3) != MATHNET_TOTAL_ROWS_V3:
    raise RuntimeError("MathNet shard row pins are internally inconsistent")


__all__ = [
    "EXPECTED_MATHNET_PILOT_CANDIDATE_COUNT_V3",
    "EXPECTED_MATHNET_PILOT_SCREEN_COUNTS_V3",
    "EXPECTED_MATHNET_PILOT_UNIVERSE_COMMITMENTS_V3",
    "EXPECTED_MATHNET_SHARDS_V3",
    "MATHNET_ARROW_SCHEMA_DIGEST_V3",
    "MATHNET_CONFIG_V3",
    "MATHNET_PHYSICAL_LEAVES_V3",
    "MATHNET_REPOSITORY_V3",
    "MATHNET_REVISION_V3",
    "MATHNET_ROW_ADAPTER_DIGEST_V3",
    "MATHNET_ROW_PROJECTION_V3",
    "MATHNET_SHARD_COUNT_V3",
    "MATHNET_SHARD_ROWS_V3",
    "MATHNET_SPLIT_V3",
    "MATHNET_TOTAL_BYTES_V3",
    "MATHNET_TOTAL_ROWS_V3",
    "ExpectedShardV3",
    "FooterAuditV3",
    "MathNetAcquisitionRuntimeEvidenceV3",
    "MathNetCacheModeV3",
    "MathNetSourceArtifactManifestV3",
    "PhysicalLeafV3",
    "PreparedMathNetPilotEvaluatorVaultV3",
    "PreparedMathNetPilotUniverseV3",
    "PreparedPinnedMathNetSourceV3",
    "VerifiedPinnedMathNetBankV3",
    "build_pinned_mathnet_bank_v3",
    "build_pinned_mathnet_pilot_artifacts_v3",
    "prepare_pinned_mathnet_source_v3",
    "verify_mathnet_remote_metadata_v3",
    "verify_pinned_mathnet_bank_v3",
    "verify_pinned_mathnet_pilot_artifacts_v3",
]
