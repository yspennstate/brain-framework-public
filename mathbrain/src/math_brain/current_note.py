"""Explicit, bounded full-note reads. No search fallback and no writable store open."""
from __future__ import annotations

import argparse
from collections import defaultdict, deque
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time

from math_brain.models import GraphNode, NodeKind, GraphEdge, EdgeKind
from math_brain.store import ResearchStore, StoreSchemaError, _default_claim_node

MAX_FAMILY_NODES = 128
MAX_FAMILY_EDGES = 512
MAX_FIELD_BYTES = 262144
MAX_ARTIFACT_BYTES = 1048576


class NoteReadError(ValueError):
    """A stable reason code; never includes withheld source text."""


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


# One historical serialization has been inspected in the live store and the
# 2026-08-12 snapshot. This is NOT a general suffix-stripping rule or truth
# promotion. Changing any stored field invalidates this exact-record binding.
_REVIEWED_LEGACY_PROJECTIONS = {'claim_0112fb9296b94e2b8050fcc526319d27': {'claim_row_sha256': '6d92bf76d3e1fba40d04096f80fdedc750bfd044cb3d27be31e180c8a7394008',
                                            'node_row_sha256': '0eb0f8e215d36ba45c4eb8e35e6fe3bc6fc6abcea8da53a36b5c31f258aa80f6',
                                            'review': 'Exact August9 citation projection; '
                                                      'August12/frozen/live row identity checked '
                                                      '2026-09-08. No theorem verification.',
                                            'suffix': ' [standard, e.g. Revuz and Yor, Continuous '
                                                      'Martingales and Brownian Motion]'},
 'claim_0325848b2a8246179f3814d358edd8ed': {'claim_row_sha256': 'c69dca6179c024f61e3023cdd61cb5353797e87dbc06f1e925053f093862ba25',
                                            'node_row_sha256': '33ea4b4e9e768ba914b30c8f92de2c0a67f532ffa177b40520462e8bfba84e3a',
                                            'review': 'Exact August9 citation projection; '
                                                      'August12/frozen/live row identity checked '
                                                      '2026-09-08. No theorem verification.',
                                            'suffix': ' [Hoffman and Wielandt (1953); see Bhatia, '
                                                      'Matrix Analysis]'},
 'claim_043a62d47e7848ed85d4cf96d929f8a0': {'claim_row_sha256': '4a927b060dc1c91908b6518d79b6f75930f548bbeb8503ba7b0a0bc17ac22da4',
                                            'node_row_sha256': '17b5c20e4683ead91b6cdbd6f3b6e8db0c0c103bd88d511ee6811f24ffa3fe38',
                                            'review': 'Exact August9 citation projection; '
                                                      'August12/frozen/live row identity checked '
                                                      '2026-09-08. No theorem verification.',
                                            'suffix': ' [standard, e.g. Folland, Real Analysis]'},
 'claim_056258f35bb34104a34b67ec9dca8f81': {'claim_row_sha256': '9e3d2c583a3a28a769f3abc90bcb4d76de41a33242c103d93f7ddb43afb5ef3c',
                                            'node_row_sha256': 'd1d86cd0db909d18a631ac673c2f6d19b7879d0bd14accc008d96e6adae5710c',
                                            'review': 'Exact August9 citation projection; '
                                                      'August12/frozen/live row identity checked '
                                                      '2026-09-08. No theorem verification.',
                                            'suffix': ' [standard, e.g. Levin and Peres, Markov '
                                                      'Chains and Mixing Times]'},
 'claim_084504062805406aad69fd9615aafd51': {'claim_row_sha256': '096de61d20cd5e747803d7f3588a7b64404825f134cbe41e55e54783631be7f1',
                                            'node_row_sha256': '7efb8a361903d5e89c0a150c7150b1ecfdfc9b081f8e8a1809ccf0d2baba6a63',
                                            'review': 'Exact August9 citation projection; '
                                                      'August12/frozen/live row identity checked '
                                                      '2026-09-08. No theorem verification.',
                                            'suffix': ' [standard, e.g. Diestel, Graph Theory]'},
 'claim_0ca712ac1e6543e8a018c4dc62b39988': {'claim_row_sha256': '4c4d407358c355d0dd6578c80b7a81f233c91cfd26d4798b758a991d20920a6c',
                                            'node_row_sha256': 'c0a20e9006b677d59a27e1821897634f6c53e3a3e257f099120504e27b29811e',
                                            'review': 'Exact August9 citation projection; '
                                                      'August12/frozen/live row identity checked '
                                                      '2026-09-08. No theorem verification.',
                                            'suffix': ' [von Neumann (1937); see Bhatia, Matrix '
                                                      'Analysis]'},
 'claim_0e7767db2b7a480fbed99584b412188c': {'claim_row_sha256': 'd757201faf79897f425623eea26f9888e37956479f3e0abe2764749010abe2cd',
                                            'node_row_sha256': '2e36c0aa4bfe08827f74b9d5ee60feb11a75d097f8897d23a65a2dfb078c7e1f',
                                            'review': 'Exact August9 citation projection; '
                                                      'August12/frozen/live row identity checked '
                                                      '2026-09-08. No theorem verification.',
                                            'suffix': ' [standard, e.g. Horn and Johnson, Matrix '
                                                      'Analysis]'},
 'claim_0e77da38a3c94698a263786b2e665977': {'claim_row_sha256': '41f085b67b6f9814dbf49e5066da81412dffa9e44c1464d5c79d08fd8d0ed5d1',
                                            'node_row_sha256': '2e0087b2ca296a7700fef59d910361e1e3b0ca5cfe64e5f7821c28404be07445',
                                            'review': 'Exact August9 citation projection; '
                                                      'August12/frozen/live row identity checked '
                                                      '2026-09-08. No theorem verification.',
                                            'suffix': ' [standard, e.g. Diestel, Graph Theory]'},
 'claim_100b35d134374740ac599b5fa20645eb': {'claim_row_sha256': '54e1c9def5e433c7160071b65ba861ac0a386e54df8ee1fa791e18a1cf581413',
                                            'node_row_sha256': 'ab89fd773eca058f533200fa1256e20e6fc7f00985df85f8cc8aed91142f1e65',
                                            'review': 'Exact August9 citation projection; '
                                                      'August12/frozen/live row identity checked '
                                                      '2026-09-08. No theorem verification.',
                                            'suffix': ' [Helly (1923); see Matousek, Lectures on '
                                                      'Discrete Geometry]'},
 'claim_11da29bae01b44caad258b0ae87f7e2c': {'claim_row_sha256': '8d4318cbf00fcf34c32ab15ba5d70fced7ce92f707b28ac1a65b24d9728d998c',
                                            'node_row_sha256': '7e9131044f45049975fb5b34fbd3e90eebd5d151c8f95f4fb177d81ee65e549e',
                                            'review': 'Exact August9 citation projection; '
                                                      'August12/frozen/live row identity checked '
                                                      '2026-09-08. No theorem verification.',
                                            'suffix': ' [Goldstine (1938); see Conway, A Course in '
                                                      'Functional Analysis]'},
 'claim_16cd3c20ee20483a974db0fe633fa1a7': {'claim_row_sha256': 'acf4db5ffdfb3f745de54435c5006c364602398767599563192b70a73ed9c7bc',
                                            'node_row_sha256': '8f55eb75fe0af31fae0b45b728c168963f695cd13f1d9373b55b4661b11ba8ec',
                                            'review': 'Exact August9 citation projection; '
                                                      'August12/frozen/live row identity checked '
                                                      '2026-09-08. No theorem verification.',
                                            'suffix': ' [standard, e.g. Conway, A Course in '
                                                      'Functional Analysis]'},
 'claim_175fd53b84b64795985d832c226ffdbd': {'claim_row_sha256': 'e60c32ba418c3844804598ad08f35deb9855ee47f6ed1e30aa30db61b4f6e660',
                                            'node_row_sha256': 'f87a9b9b89046ba58c4b9bcf21679535298947ceea7a17bc97ccabc1b657f32e',
                                            'review': 'Exact August9 citation projection; '
                                                      'August12/frozen/live row identity checked '
                                                      '2026-09-08. No theorem verification.',
                                            'suffix': ' [Birkhoff (1946); von Neumann (1953); see '
                                                      'Horn and Johnson, Matrix Analysis]'},
 'claim_1926cb417a234ddcbbb429d8fa78f0ae': {'claim_row_sha256': '0aa3db95c082047903371f57e45741bc41c4e7acf065eae5eb41e0498250816f',
                                            'node_row_sha256': '8d6e48aab7a0f73a8e6222175acb64173650882f33ed089a909cc0c3d9ae810c',
                                            'review': 'Exact August9 citation projection; '
                                                      'August12/frozen/live row identity checked '
                                                      '2026-09-08. No theorem verification.',
                                            'suffix': ' [Gelfand (1941); see Conway, A Course in '
                                                      'Functional Analysis]'},
 'claim_19c18ec50ffc4122b7f4ab13e47ea9c5': {'claim_row_sha256': 'b7e993118886d62039e4866908fe91ac76cc828b2cdd93e6ff3518b49b90ac3d',
                                            'node_row_sha256': '2a4d3bbe33bbcd3d076a88a5e2994984151dd550f0bed7a4e38df85f2e915d24',
                                            'review': 'Exact August9 citation projection; '
                                                      'August12/frozen/live row identity checked '
                                                      '2026-09-08. No theorem verification.',
                                            'suffix': ' [standard, e.g. Horn and Johnson, Matrix '
                                                      'Analysis]'},
 'claim_19dcaed4a4b54d26a4e8fa06af239488': {'claim_row_sha256': 'a053313315b314c4d010e1df4ca251358a710bfcffb6e8a2ba88c70c88995971',
                                            'node_row_sha256': '778f4d72fdf81462b10bb9f279e876d09ce51eb95fc6e58907d55a415931b7c8',
                                            'review': 'Exact August9 citation projection; '
                                                      'August12/frozen/live row identity checked '
                                                      '2026-09-08. No theorem verification.',
                                            'suffix': ' [standard, e.g. Conway, A Course in '
                                                      'Functional Analysis]'},
 'claim_1a3bd4aa60424ad693747cda2be853bf': {'claim_row_sha256': '463e41e4127543019f4d7492d638f43d0073250aec7aa861ad5daf5e1d736b71',
                                            'node_row_sha256': '24168ec9c9e6c7359de0f4b2577b6214c791c286a3e20d7d69452d939f2a4984',
                                            'review': 'Exact August9 citation projection; '
                                                      'August12/frozen/live row identity checked '
                                                      '2026-09-08. No theorem verification.',
                                            'suffix': ' [standard, e.g. Horn and Johnson, Matrix '
                                                      'Analysis]'},
 'claim_1c69a4234bf2480fa981cc45dd4e4fcc': {'claim_row_sha256': 'bf0625f0deb9c907c06495eecd78e3b086548e20bf2926efdad8bad61bdef112',
                                            'node_row_sha256': '9b223b480d79f11e29d7425b0408dfcdf431ab653e071bb385194c7df39e5923',
                                            'review': 'Exact August9 citation projection; '
                                                      'August12/frozen/live row identity checked '
                                                      '2026-09-08. No theorem verification.',
                                            'suffix': ' [standard, e.g. Rudin, Principles of '
                                                      'Mathematical Analysis]'},
 'claim_1dcc357337ac40408d158acb1fbfe9d6': {'claim_row_sha256': 'bb5234db10e8ba79968b7768f3f9ea3570b2ac27efc80724caff0e4bf5431eae',
                                            'node_row_sha256': '487d8179d9c661c9caff8a03620f4f000e97e7bf86c353bc1fb96ca193382891',
                                            'review': 'Exact August9 citation projection; '
                                                      'August12/frozen/live row identity checked '
                                                      '2026-09-08. No theorem verification.',
                                            'suffix': ' [Durrett, Probability: Theory and '
                                                      'Examples]'},
 'claim_1de6adaf9b8d4dcda120ad425f8242e4': {'claim_row_sha256': 'c56bcb46f0b7827e1697b91193f41a61bc25d0c552bcbd9643a9d086515c5f60',
                                            'node_row_sha256': '0eccde9111440d1535640e7605e0dfdc523b13a115a21eb8dc9a80577bb84aca',
                                            'review': 'Exact August9 citation projection; '
                                                      'August12/frozen/live row identity checked '
                                                      '2026-09-08. No theorem verification.',
                                            'suffix': ' [standard, e.g. Horn and Johnson, Matrix '
                                                      'Analysis]'},
 'claim_206681fba2f54f748243e472801a5475': {'claim_row_sha256': 'cbd9c602508660eac9112be700d46dc492ec3c4a6622125702c52826f08e3823',
                                            'node_row_sha256': 'a73788afc52493703551678c8fa70616494455b2f64af4727f43d2c467f67795',
                                            'review': 'Exact August9 citation projection; '
                                                      'August12/frozen/live row identity checked '
                                                      '2026-09-08. No theorem verification.',
                                            'suffix': ' [standard, e.g. Diestel, Graph Theory]'},
 'claim_21a21b95cf1e4f55b790a53b876e443d': {'claim_row_sha256': '7bd13bb06e89e4add3ea1e7a2802557bd8f27f19f85cd51fde7aff9259e5c720',
                                            'node_row_sha256': '8c96901aa0cdd77a40a181c08c7facd8c60d6eec9203f43456339dab484d8ad0',
                                            'review': 'Exact August9 citation projection; '
                                                      'August12/frozen/live row identity checked '
                                                      '2026-09-08. No theorem verification.',
                                            'suffix': ' [standard, e.g. Alon and Spencer, The '
                                                      'Probabilistic Method]'},
 'claim_23516ac3d3e64fd5bdf74e878ce879d6': {'claim_row_sha256': 'b60d4da31371908e1d91c744132b7fba31c8b7f00f458ecd5adf69fffbd9c18f',
                                            'node_row_sha256': '7053b82d123c8c84dc237fa453e05bc35d31d11f1733236789f8c65950ba9cd5',
                                            'review': 'Exact August9 citation projection; '
                                                      'August12/frozen/live row identity checked '
                                                      '2026-09-08. No theorem verification.',
                                            'suffix': ' [Levin and Peres, Markov Chains and Mixing '
                                                      'Times]'},
 'claim_26bfe97e36c54d40ab97e4665a8ce2a4': {'claim_row_sha256': 'a658d6e0378c2fe2aada9cb59609b655ec5669db95d15b51eb937bf85f2b1534',
                                            'node_row_sha256': 'd0c4dae01a60d12869a44f493cdb6afa41e1a5e443d4e38f558796c3d733ce31',
                                            'review': 'Exact August9 citation projection; '
                                                      'August12/frozen/live row identity checked '
                                                      '2026-09-08. No theorem verification.',
                                            'suffix': ' [Erdos and Gallai (1960); see West, '
                                                      'Introduction to Graph Theory]'},
 'claim_28f2e4f1ebef48b8acea917db6900b4b': {'claim_row_sha256': '4466ce1de1b01ef0d055977e0f898bf605d00d00b010f8dc147cd5b2f55643ab',
                                            'node_row_sha256': '47afd6d17873571c195b1ececa750443fa210f4149f5bd33cc644b55a504b30a',
                                            'review': 'Exact August9 citation projection; '
                                                      'August12/frozen/live row identity checked '
                                                      '2026-09-08. No theorem verification.',
                                            'suffix': ' [standard, e.g. Horn and Johnson, Matrix '
                                                      'Analysis]'},
 'claim_2aa9335e287f40f68e7a34eff0219e47': {'claim_row_sha256': '8f3a99d9f48d1114607ee6a0607e9cd0612c8432ca9471ba8104de1c05e3c499',
                                            'node_row_sha256': '6cae2807a0ce886747ae9d342508023e48b0305400cc9632fd8c25e21173448d',
                                            'review': 'Exact August9 citation projection; '
                                                      'August12/frozen/live row identity checked '
                                                      '2026-09-08. No theorem verification.',
                                            'suffix': ' [Ky Fan (1949); see Bhatia, Matrix '
                                                      'Analysis]'},
 'claim_2ab6123e445443e08bd6791ffc634d6d': {'claim_row_sha256': '89faa9ec3272a4a7f37b9d69ba7262c2f9b0edb34eb8e8b2b684622755b56802',
                                            'node_row_sha256': '3a9c1d5f7aee8bcfd494e91cba5c3a0cd2524fec62d77388dbc338a52df25ef0',
                                            'review': 'Exact August9 citation projection; '
                                                      'August12/frozen/live row identity checked '
                                                      '2026-09-08. No theorem verification.',
                                            'suffix': ' [standard, e.g. Diestel, Graph Theory]'},
 'claim_2ab7149509bc4f319eff099c782bec07': {'claim_row_sha256': '51e431d705bad3b8e1f2667f3c5b27d61de87e677550ad6954f53107f18d8352',
                                            'node_row_sha256': 'c8d4511e7caa8e58963606aaad88382aae800798ae0e99d1b1b39bffa1ee9230',
                                            'review': 'Exact August9 citation projection; '
                                                      'August12/frozen/live row identity checked '
                                                      '2026-09-08. No theorem verification.',
                                            'suffix': ' [Durrett, Probability: Theory and '
                                                      'Examples]'},
 'claim_2c147bad27314713b7c80e7392078581': {'claim_row_sha256': '32a8f70a5257a491f09af44fbb0bdd704c59d5264bf0deaff75096b8e4cf96af',
                                            'node_row_sha256': '9322cecc7713ab723bb12bb1bf70eab7c602be65a7825e816ea689eeb7b16b25',
                                            'review': 'Exact August9 citation projection; '
                                                      'August12/frozen/live row identity checked '
                                                      '2026-09-08. No theorem verification.',
                                            'suffix': ' [standard, e.g. Hell and Nesetril, Graphs '
                                                      'and Homomorphisms]'},
 'claim_2f50ad79a2004f7badc843b252c522f8': {'claim_row_sha256': '2ae0209d3703128cb24ba134b4834143c9a46ab71f8d8e59a0eb130bbacd8746',
                                            'node_row_sha256': 'deef69a6c4d4d07d167663449ade7f218696ac22d563064301d2945e6038f2c6',
                                            'review': 'Exact August9 citation projection; '
                                                      'August12/frozen/live row identity checked '
                                                      '2026-09-08. No theorem verification.',
                                            'suffix': ' [standard, e.g. Muirhead, Aspects of '
                                                      'Multivariate Statistical Theory]'},
 'claim_374ecdacc6264b4aadc0d74d66687c5d': {'claim_row_sha256': '52ff7f6774c2240c7482c1a53af8607c7129e9c76d7abf1effc4d4d33f64d910',
                                            'node_row_sha256': '214bbffab9e2680b1c0e61b3cf7b61ac1282a3771a793dfb33d5e5d5766b1cfe',
                                            'review': 'Exact August9 citation projection; '
                                                      'August12/frozen/live row identity checked '
                                                      '2026-09-08. No theorem verification.',
                                            'suffix': ' [standard, e.g. Durrett, Probability: '
                                                      'Theory and Examples, Appendix]'},
 'claim_3b3fcad33f7241cbac6d308131ba82b6': {'claim_row_sha256': '725f874c54992048d5afb99eba87c30c447ca1d9ba85e3f71ce72cc17d31b2a5',
                                            'node_row_sha256': '109aafd05a455e7e2aeed634c21020b769c55249bb2a3c94e769d6db736de433',
                                            'review': 'Exact August9 citation projection; '
                                                      'August12/frozen/live row identity checked '
                                                      '2026-09-08. No theorem verification.',
                                            'suffix': ' [Dirac (1952); see Diestel, Graph Theory]'},
 'claim_3dcb9b99a4d5476a9c1ff6f1dac42640': {'claim_row_sha256': 'd9e844563d392f04b4472c21a9b9989511ef62a417e6cfdac191ec35f14c8d5e',
                                            'node_row_sha256': 'bbf2b49733a053229830aeda13e9234f73d67750ebecb1377bf7ad12688c4b13',
                                            'review': 'Exact August9 citation projection; '
                                                      'August12/frozen/live row identity checked '
                                                      '2026-09-08. No theorem verification.',
                                            'suffix': ' [standard, e.g. Horn and Johnson, Matrix '
                                                      'Analysis]'},
 'claim_3e88686fef6341788c323170fcda7e02': {'claim_row_sha256': '0de36eef202e809fd7dcc7a630e7b4a65b91a109bfa3e252800074433065e2ca',
                                            'node_row_sha256': '9954ab847efaf1b2bf9565fe40cecc132757c868b4410586e1a43bb17ddc3785',
                                            'review': 'Exact August9 citation projection; '
                                                      'August12/frozen/live row identity checked '
                                                      '2026-09-08. No theorem verification.',
                                            'suffix': ' [standard, e.g. Vershynin, '
                                                      'High-Dimensional Probability]'},
 'claim_3f2a0dd1b0b44009884d6b1b3034c46c': {'claim_row_sha256': '47a6dbb6abb76997e1cf14a76eca750e4a007670bf5cf7c3dd14c46bc8a32442',
                                            'node_row_sha256': 'f0f48cd2c3164c26e01d15f6db8d6e1de19d71ed490587c1db10dbd00246a2d9',
                                            'review': 'Exact August9 citation projection; '
                                                      'August12/frozen/live row identity checked '
                                                      '2026-09-08. No theorem verification.',
                                            'suffix': ' [Bonferroni (1936); see Alon and Spencer, '
                                                      'The Probabilistic Method]'},
 'claim_40d136ffd15d4b48aa6fcfdde08e6f3e': {'claim_row_sha256': '7637029c152b893f99f4c1fb66256e8556d40cd75f4cdb0edcdba8faaa4e5676',
                                            'node_row_sha256': 'bb4d58933918b2f944ad48a3c7609cc6977f66f90938084dd4b9d50150dd59cd',
                                            'review': 'Exact August9 citation projection; '
                                                      'August12/frozen/live row identity checked '
                                                      '2026-09-08. No theorem verification.',
                                            'suffix': ' [standard, e.g. Godsil and Royle, '
                                                      'Algebraic Graph Theory]'},
 'claim_411a53c5875b46598dfa12917206a931': {'claim_row_sha256': '445295a19e09745cb74c6e1d93d1bfd4326e28bb90edf91ff4fcbbbda401e51c',
                                            'node_row_sha256': '7b6e2a3b8549dd78ed445e3420c91dac7e5ab9ad95f7db2063489f671d6f6cd6',
                                            'review': 'Exact August9 citation projection; '
                                                      'August12/frozen/live row identity checked '
                                                      '2026-09-08. No theorem verification.',
                                            'suffix': ' [standard, e.g. Horn and Johnson, Matrix '
                                                      'Analysis]'},
 'claim_4146bfed2cef483883ddd4ccdadd743e': {'claim_row_sha256': '9bfa11e14b9168b125568b11068870aa408f54605fdb0c9a86b445c21396d222',
                                            'node_row_sha256': '5a5a73e34fac2301118c19cadc40aad4e90a2ffc9aa238e6ef5998cc7b2c376a',
                                            'review': 'Exact August9 citation projection; '
                                                      'August12/frozen/live row identity checked '
                                                      '2026-09-08. No theorem verification.',
                                            'suffix': ' [standard, e.g. Williams, Probability with '
                                                      'Martingales]'},
 'claim_424e66e20cd94b9385307c1a9ced9a4a': {'claim_row_sha256': 'ea059708edc309ab180e09f52a406ce5b093493fe0819b46b2ccf73ff3fbf3eb',
                                            'node_row_sha256': '425a24bac6e458fca492e39f9fc08556c58dfaa0e80200dff223eb43f2aaebea',
                                            'review': 'Exact August9 citation projection; '
                                                      'August12/frozen/live row identity checked '
                                                      '2026-09-08. No theorem verification.',
                                            'suffix': ' [standard, e.g. Horn and Johnson, Matrix '
                                                      'Analysis]'},
 'claim_4408a4166bc34384a3c6442b8ba59a69': {'claim_row_sha256': 'aee7d1f346ee1df27b4ac1efc64f7a7a31cf24c5fe21ce907442351c016285ac',
                                            'node_row_sha256': '8e041d13ed32df01eb17b7f2f04d8d9929a4d2c5c4430cc1294ad11eb9873264',
                                            'review': 'Exact August9 citation projection; '
                                                      'August12/frozen/live row identity checked '
                                                      '2026-09-08. No theorem verification.',
                                            'suffix': ' [Bollobas (1965); see Jukna, Extremal '
                                                      'Combinatorics]'},
 'claim_4413c75b4893462a882c365470f9d959': {'claim_row_sha256': 'c1e01e233a3f717cfe0e3ff8677fcb6e7526f71814f6f27ba149c0dd3da8f12f',
                                            'node_row_sha256': '644dda350c781b8d36caa5139cdb78c3cc8e81e8a07d024f02062d1a3d4b29f7',
                                            'review': 'Exact August9 citation projection; '
                                                      'August12/frozen/live row identity checked '
                                                      '2026-09-08. No theorem verification.',
                                            'suffix': ' [standard, e.g. Evans, Partial '
                                                      'Differential Equations]'},
 'claim_48ee9fb099d640f7a7436a063d5abdda': {'claim_row_sha256': '6d32e6f0398f89649f1da6b2f895508497f773e8d1df627d3c5def2baee9cb48',
                                            'node_row_sha256': 'efe303d23f71d92976288e0be931218cc042c8d7486aa68f8d9f1e4e0fe74233',
                                            'review': 'Exact August9 citation projection; '
                                                      'August12/frozen/live row identity checked '
                                                      '2026-09-08. No theorem verification.',
                                            'suffix': ' [James (1964, 1972); see Conway, A Course '
                                                      'in Functional Analysis]'},
 'claim_5205129c95544809a8efccd77002ac5d': {'claim_row_sha256': '5c09f50bf5fa252a810e87dcbbbf0c4ffaea11c9afcfd9f0e8f75bd61c10dfe0',
                                            'node_row_sha256': '8b3e4eec54618cb345c86bfea0bbca1bbc8f0eb1d3c7ab8724620588668961b1',
                                            'review': 'Exact August9 citation projection; '
                                                      'August12/frozen/live row identity checked '
                                                      '2026-09-08. No theorem verification.',
                                            'suffix': ' [standard, e.g. Horn and Johnson, Matrix '
                                                      'Analysis]'},
 'claim_52e3b76a798d4a238bb16dc3bd4038c9': {'claim_row_sha256': '020208653b7c6a86e3c2d5835bdb01fcd56bb7a35dff67578b5038c63bac792a',
                                            'node_row_sha256': '55829ad824169271844a1e694637a980eae80a9e9c433c50ea805d6ca05c0004',
                                            'review': 'Exact August9 citation projection; '
                                                      'August12/frozen/live row identity checked '
                                                      '2026-09-08. No theorem verification.',
                                            'suffix': ' [standard, e.g. Horn and Johnson, Matrix '
                                                      'Analysis]'},
 'claim_5358e2ea0c524a1a9ea3779d7b096a63': {'claim_row_sha256': 'c7783a9d2ea750d6df00bff4909ce45765d141fd6b331fed8a6dcddc598e11b8',
                                            'node_row_sha256': '22f96dbb6f176722cc9c648d8bd08cb408b6524b76de6e64c5006a50bae899ad',
                                            'review': 'Exact August9 citation projection; '
                                                      'August12/frozen/live row identity checked '
                                                      '2026-09-08. No theorem verification.',
                                            'suffix': ' [standard, e.g. Hoory, Linial and '
                                                      'Wigderson, Expander Graphs and Their '
                                                      'Applications]'},
 'claim_535a09ff0ea043e28153f1f1760c5da3': {'claim_row_sha256': 'ee8d6c6cbc183db04cd13ad23525812cba012224a7ecc26746acdd86e0d81731',
                                            'node_row_sha256': '99db700f318888bbf5d53e036a9308b218f6f5e8fbf4b7e8b489b3746c3d3378',
                                            'review': 'Exact August9 citation projection; '
                                                      'August12/frozen/live row identity checked '
                                                      '2026-09-08. No theorem verification.',
                                            'suffix': ' [Ginibre (1965), Mehta; see Bai and '
                                                      'Silverstein, Spectral Analysis of Large '
                                                      'Dimensional Random Matrices]'},
 'claim_53bde2ed982f4c4fa6f7e1a458f14ec6': {'claim_row_sha256': '28b54ca69358248f47bc6018fe7f71e97071d9afc326a4070f43bbf65e1c528a',
                                            'node_row_sha256': 'c7cb2dd7cc3c3229b4087e824ae68887d53f0f36e25aea5d0dd0d3899251d8a7',
                                            'review': 'Exact August9 citation projection; '
                                                      'August12/frozen/live row identity checked '
                                                      '2026-09-08. No theorem verification.',
                                            'suffix': ' [Durrett, Probability: Theory and '
                                                      'Examples]'},
 'claim_58bc2e019d5c41d686fda3087ff61e3e': {'claim_row_sha256': 'ee52af1e6a4936ea8e20182ab51703f0fbe83b5f1c4efc6fde6ee12571d235c7',
                                            'node_row_sha256': '3bdbbd30a27b31bf8eecbe2f6d52f63f421a499dbf2c192b18f68f7585542bf8',
                                            'review': 'Exact August9 citation projection; '
                                                      'August12/frozen/live row identity checked '
                                                      '2026-09-08. No theorem verification.',
                                            'suffix': ' [standard, e.g. Durrett, Probability: '
                                                      'Theory and Examples]'},
 'claim_58ea013fb13a44c4a5353c75f447fc2b': {'claim_row_sha256': '3d62b1d9e0ec4997a81963730379f0bf35c690fc3a61dbf1bf5a0d9096b23b29',
                                            'node_row_sha256': 'f11ac8bd732fe5c8d36a1a11f10b40ff7358fb54c64bafc0a9745f3adc704ebf',
                                            'review': 'Exact August9 citation projection; '
                                                      'August12/frozen/live row identity checked '
                                                      '2026-09-08. No theorem verification.',
                                            'suffix': ' [standard, e.g. Boucheron, Lugosi and '
                                                      'Massart, Concentration Inequalities]'},
 'claim_5b07117127fc43f3a8f0e19b21d0529f': {'claim_row_sha256': '133d3f4c681539bf618f264bb5635e6c670426e3ad39f9e702dccb3b2bfcae1b',
                                            'node_row_sha256': 'ab314a0502ad7e332285795d7ff0a7dc0cbbddc7981065eb1f9510131b98695b',
                                            'review': 'Exact August9 citation projection; '
                                                      'August12/frozen/live row identity checked '
                                                      '2026-09-08. No theorem verification.',
                                            'suffix': ' [standard, e.g. Horn and Johnson, Matrix '
                                                      'Analysis]'},
 'claim_5b6060c32d44472192b40195a1ab6283': {'claim_row_sha256': 'cd546eb307ec3dc2beb7f31f08350ccc4cbf014b3da954d735ff168d4f0b61ed',
                                            'node_row_sha256': '0da5ed6e36b0f85ea9b2ac92cb516e2edd977fea7764db5a84bf93f3e9a8e260',
                                            'review': 'Exact August9 citation projection; '
                                                      'August12/frozen/live row identity checked '
                                                      '2026-09-08. No theorem verification.',
                                            'suffix': ' [Cauchy (1813); Davenport (1935); see Tao '
                                                      'and Vu, Additive Combinatorics]'},
 'claim_615888e23ec44a40bbcbb252b07cd897': {'claim_row_sha256': '99627f6a104683fabe5b874270e73f735899c3878faf250c5e560f34a4b898d3',
                                            'node_row_sha256': '9c8667eb167b3301be256431d3be959507e90043cd1fdcf514f7cd2777574ca9',
                                            'review': 'Exact August9 citation projection; '
                                                      'August12/frozen/live row identity checked '
                                                      '2026-09-08. No theorem verification.',
                                            'suffix': ' [standard, e.g. Brezis, Functional '
                                                      'Analysis, Sobolev Spaces and PDEs]'},
 'claim_65df5d655e8a4eba9f2c3a7e08aabe83': {'claim_row_sha256': 'b7f781c6518f36bf0c2d6366f2fbfb769aa45ac8a1e69e91dcc8c1e853f0483d',
                                            'node_row_sha256': '8d49d6ae1c0328b900b585050ed0f746092c85ffd01243029a6ba98410bb000c',
                                            'review': 'Exact August9 citation projection; '
                                                      'August12/frozen/live row identity checked '
                                                      '2026-09-08. No theorem verification.',
                                            'suffix': ' [Durrett, Probability: Theory and '
                                                      'Examples]'},
 'claim_6624cdeb6fa248508a462ea342ee767f': {'claim_row_sha256': 'c664f584633f9dc1adb16bdf60c9a1ade337d84a2162fcbb83088ca3159d9b1e',
                                            'node_row_sha256': 'd19b819918e9c8f37d889e58de4e08154f6f42c972474fdebeec4bcd540a31a9',
                                            'review': 'Exact August9 citation projection; '
                                                      'August12/frozen/live row identity checked '
                                                      '2026-09-08. No theorem verification.',
                                            'suffix': ' [Erdos and Szekeres (1935); see Bollobas, '
                                                      'Combinatorics]'},
 'claim_66bbf5d848914b8faf485244661ff8ea': {'claim_row_sha256': '143533f352d1c1c09a647c029e77065fa9f04e891d3436da480f49208e9ac13e',
                                            'node_row_sha256': '9158fe5cc4b76224c5adba82310452d7f61246843ed88b731d625fb9d39cc5a2',
                                            'review': 'Exact August9 citation projection; '
                                                      'August12/frozen/live row identity checked '
                                                      '2026-09-08. No theorem verification.',
                                            'suffix': ' [standard, e.g. Pazy, Semigroups of Linear '
                                                      'Operators and Applications to Partial '
                                                      'Differential Equations]'},
 'claim_6d50f8e9f6e14d4fba06010f9764ef25': {'claim_row_sha256': '7c38711e3f3c980959c88ee85000f10df6217defba257ee8483525f6b2ed145e',
                                            'node_row_sha256': '23987604eb33dcc9d9ae0813fdbb6ae0bbed8fb59f17f741ac4dbfc0fab64ff8',
                                            'review': 'Exact August9 citation projection; '
                                                      'August12/frozen/live row identity checked '
                                                      '2026-09-08. No theorem verification.',
                                            'suffix': ' [standard, e.g. Halmos and Sunder, Bounded '
                                                      'Integral Operators on L2 Spaces]'},
 'claim_73e2f19c42014dbc9c06e7150ca78859': {'claim_row_sha256': 'dea4f32b55f4a743cc8276a53805327fffbe2e564566b75d6b8521975017ee31',
                                            'node_row_sha256': '3b3fa0b4d12f2e451103cadfc19d867f9e81b8b1ed1590a7cd83fb48bd0326df',
                                            'review': 'Exact August9 citation projection; '
                                                      'August12/frozen/live row identity checked '
                                                      '2026-09-08. No theorem verification.',
                                            'suffix': ' [Durrett, Probability: Theory and '
                                                      'Examples]'},
 'claim_7c369353b48a4d8ba08890496be965f7': {'claim_row_sha256': '93c965c746709104e2961e9cb9f48a4e2030b9a56948d5cade96b4f13792e29e',
                                            'node_row_sha256': '604fc8546a3c86ca3bbe77b908638efbeeb0b3a8d61884e5d55f805e161b891a',
                                            'review': 'Exact August9 citation projection; '
                                                      'August12/frozen/live row identity checked '
                                                      '2026-09-08. No theorem verification.',
                                            'suffix': ' [standard, e.g. Horn and Johnson, Matrix '
                                                      'Analysis]'},
 'claim_80df6b72af3f42b3b905dca6f87a0116': {'claim_row_sha256': 'a044483088f30ff814960721af18fa69ac75f4d59a6c3430098ac554434a5587',
                                            'node_row_sha256': 'b2b91d0c97e92ba3feb0e5f3737c024000c25e1be16fcf68e5b55dacefcfc263',
                                            'review': 'Exact August9 citation projection; '
                                                      'August12/frozen/live row identity checked '
                                                      '2026-09-08. No theorem verification.',
                                            'suffix': ' [standard, e.g. Horn and Johnson, Topics '
                                                      'in Matrix Analysis]'},
 'claim_820ccbbfa69c4e358132bdde6738428a': {'claim_row_sha256': '611306d94616a208708abf48f1b50598c2f19c98151aa84cceebcfc018ce1822',
                                            'node_row_sha256': 'abbb1a7bbee0dffa2fc24dbf0f616c6574798ee0d9e2d50972075ec83cea5daf',
                                            'review': 'Exact August9 citation projection; '
                                                      'August12/frozen/live row identity checked '
                                                      '2026-09-08. No theorem verification.',
                                            'suffix': ' [standard, e.g. Conway, A Course in '
                                                      'Functional Analysis]'},
 'claim_83b9118a2b424fa28f587c0a4458e368': {'claim_row_sha256': 'eaaebe93eee9ec00bb8bec4fe7a400fa6550b2186f3b3bfae253ffa166da74f8',
                                            'node_row_sha256': '3cf8fbd96aad2e1d8bb5811c50e9d3284841f1cf0a61af4026cee8c2eddd8d0b',
                                            'review': 'Exact August9 citation projection; '
                                                      'August12/frozen/live row identity checked '
                                                      '2026-09-08. No theorem verification.',
                                            'suffix': ' [standard, e.g. Horn and Johnson, Topics '
                                                      'in Matrix Analysis]'},
 'claim_85fd660c6da64a8ba172c19505215223': {'claim_row_sha256': 'da026b58e6d7e479734a22c6ebb3b7c6c27f73467ae15cb5d302c94551288fc8',
                                            'node_row_sha256': 'cf7a5311bfe05e5bb31100cabebaf5699cbd209f76ce7f863fabdf6985b41857',
                                            'review': 'Exact August9 citation projection; '
                                                      'August12/frozen/live row identity checked '
                                                      '2026-09-08. No theorem verification.',
                                            'suffix': ' [standard, e.g. Brezis, Functional '
                                                      'Analysis, Sobolev Spaces and PDEs]'},
 'claim_871fa5f6c1794c47b996b4afb3f79904': {'claim_row_sha256': 'a071e8108a82f702571924da95d4be0b98088ef3b13a22c624db9a6188343721',
                                            'node_row_sha256': 'ce2bf6d8785d0414c35faac284bd1e8a87898b35cd7fee200080ba22310bb879',
                                            'review': 'Exact August9 citation projection; '
                                                      'August12/frozen/live row identity checked '
                                                      '2026-09-08. No theorem verification.',
                                            'suffix': ' [Billingsley, Convergence of Probability '
                                                      'Measures]'},
 'claim_8741e7fb0c624a819ab1ecbebe8af364': {'claim_row_sha256': '3e53c8f07fabfed1158acea330cb64e7238870ecce1e1fdb37f415b576bfecdf',
                                            'node_row_sha256': '7971176e3db69fc17b91fa73add8e9ba3352a75ddf2ad85993cc0310df2a1630',
                                            'review': 'Exact August9 citation projection; '
                                                      'August12/frozen/live row identity checked '
                                                      '2026-09-08. No theorem verification.',
                                            'suffix': ' [standard, e.g. Gray, Toeplitz and '
                                                      'Circulant Matrices: A Review]'},
 'claim_8775cf067f434eefbd277848b29940d9': {'claim_row_sha256': '7198c19a3a18c5bb1359b00225632127d4833348a913911969c10408fa4453d5',
                                            'node_row_sha256': 'c10c1af6f15fbdc3bd2d6b0b6dc57ef18ac437a1eaba8a39ee6bfb25de03a90b',
                                            'review': 'Exact August9 citation projection; '
                                                      'August12/frozen/live row identity checked '
                                                      '2026-09-08. No theorem verification.',
                                            'suffix': ' [standard, e.g. Folland, Real Analysis]'},
 'claim_8d38bf7170b240659bb066df03da08a1': {'claim_row_sha256': '5ff144420fb5d16b6010634d0715e1cff29629d16d27a8835ea47ab85633a2a9',
                                            'node_row_sha256': '4251dcb3518bd3b8ea45f7d5dc2a51961eae4dfb286c624d4fd418ed5fa9325d',
                                            'review': 'Exact August9 citation projection; '
                                                      'August12/frozen/live row identity checked '
                                                      '2026-09-08. No theorem verification.',
                                            'suffix': ' [standard, e.g. Conway, A Course in '
                                                      'Functional Analysis]'},
 'claim_8f86e244984a4da294c8238db1fa3aa9': {'claim_row_sha256': 'b08e68d70c5a597b50384c1517024ab16f08b50764fdd6a116ffb3d761bd7240',
                                            'node_row_sha256': '49ff6c82245950aa544b4dabc69842dc698ef11a6387664b850b377602dace0d',
                                            'review': 'historical Schur-complement record '
                                                      'projection, 2026-09-08',
                                            'suffix': ' [standard, e.g. Boyd and Vandenberghe, '
                                                      'Convex Optimization, Appendix A]'},
 'claim_93a538f9b74c4a7da582fc782bd127c1': {'claim_row_sha256': 'b8c21c7cf9d8a61ba1251452b1f2a27feef4a79295d91690786695cb61a4b68d',
                                            'node_row_sha256': 'e7919fd3b210ad954f17f13b5bb2016a18b561e51fada52b3532270e06300b0c',
                                            'review': 'Exact August9 citation projection; '
                                                      'August12/frozen/live row identity checked '
                                                      '2026-09-08. No theorem verification.',
                                            'suffix': ' [standard, e.g. Horn and Johnson, Matrix '
                                                      'Analysis]'},
 'claim_986b25f6f60a4cec9e0f0b235d97ddd8': {'claim_row_sha256': '8ea2aa739489522757824916016b215a5f6db5a39da3a485183924af433368fe',
                                            'node_row_sha256': 'a7e284eadfa6c8ba11ce5d72714c118203992f32bd13f32c6cf2056e4a70abf9',
                                            'review': 'Exact August9 citation projection; '
                                                      'August12/frozen/live row identity checked '
                                                      '2026-09-08. No theorem verification.',
                                            'suffix': " [Chvatal (1975); see O'Rourke, Art Gallery "
                                                      'Theorems and Algorithms]'},
 'claim_98c392b4dc0442e2957afe98abafa589': {'claim_row_sha256': '91761cfa6f9ddf45d8d1713a95524dc80c0f5bc37e5cbbe2dd7f678589b6fcbb',
                                            'node_row_sha256': 'd6a5e342e5e0360227df5a928383ce70ba1ba024d0cd78241f671d25d81efe58',
                                            'review': 'Exact August9 citation projection; '
                                                      'August12/frozen/live row identity checked '
                                                      '2026-09-08. No theorem verification.',
                                            'suffix': ' [standard, e.g. Durrett, Probability: '
                                                      'Theory and Examples]'},
 'claim_9ce9a58a8b3e4adeabe2e3ea21c5f9ba': {'claim_row_sha256': 'c3fd170606aa1b71778b58595f2fc31d8ffb45b0d4c61919618423fa0a0aed78',
                                            'node_row_sha256': '19d859d3459dbaca07aa0fe7412c2c6fe4c8ee1aed9faa3bb67ed388764b394e',
                                            'review': 'Exact August9 citation projection; '
                                                      'August12/frozen/live row identity checked '
                                                      '2026-09-08. No theorem verification.',
                                            'suffix': ' [Vizing (1964); see Diestel, Graph '
                                                      'Theory]'},
 'claim_a31a497f2f5d4c6f85e1924ac93741b7': {'claim_row_sha256': '5dfa10efeec8b242ffaf36ac56d35a2f74eb16ef8e0e3d729ddefb8960486627',
                                            'node_row_sha256': '64657b21f78bfecc34df908da328499d84d795182b1cd85a5d8d6158fb0910fb',
                                            'review': 'Exact August9 citation projection; '
                                                      'August12/frozen/live row identity checked '
                                                      '2026-09-08. No theorem verification.',
                                            'suffix': ' [Ruzsa and Szemeredi (1976); see Alon and '
                                                      'Spencer, The Probabilistic Method]'},
 'claim_a63e019b7ba74f299928318e71eae8f4': {'claim_row_sha256': 'ade4306e44db0134f6c37cd01e9b9deeae972ff51d4f70569bd2d8f0630a0714',
                                            'node_row_sha256': 'a694a088c85fe3653375ec9c4cbcfe0bcc6f6318f8cf58dbcda0e432d0b25c6c',
                                            'review': 'Exact August9 citation projection; '
                                                      'August12/frozen/live row identity checked '
                                                      '2026-09-08. No theorem verification.',
                                            'suffix': ' [standard, e.g. Williams, Probability with '
                                                      'Martingales]'},
 'claim_ad4eb0ee684f46bf820890f370859daa': {'claim_row_sha256': 'fba880fb1c6a2ac96a5f5417adb2a941d0a3eff00caf9101095edde8e4718580',
                                            'node_row_sha256': '4ec7dddfa5a11e172f3582bfafe90eaa318d1262f670756c6afe15ccb55c73ce',
                                            'review': 'Exact August9 citation projection; '
                                                      'August12/frozen/live row identity checked '
                                                      '2026-09-08. No theorem verification.',
                                            'suffix': ' [Clarkson (1936); see Brezis, Functional '
                                                      'Analysis, Sobolev Spaces and PDEs]'},
 'claim_aeeb4c10ceed45d7af1584f36ad675d6': {'claim_row_sha256': '8568d3fa5aabca4534e680b8534b229b6e06fd6896c897ac193fa1542be840b5',
                                            'node_row_sha256': '4cf59173f18af7473710c4c5cafc699c83c14fac0e5b4ddc093e436be6690429',
                                            'review': 'Exact August9 citation projection; '
                                                      'August12/frozen/live row identity checked '
                                                      '2026-09-08. No theorem verification.',
                                            'suffix': ' [Erdos, Renyi and Sos (1966)]'},
 'claim_af0032d41b354831aa25ff56a4e44ce9': {'claim_row_sha256': 'eef18c4b05951a81f00d6a7a589f5d9bb951c5cea22d775e233c81b3a2a84454',
                                            'node_row_sha256': '4b3ae2bf01b63ec4d5b914ea6da74bf1f0674192262a8c97ee8874526f4535b6',
                                            'review': 'Exact August9 citation projection; '
                                                      'August12/frozen/live row identity checked '
                                                      '2026-09-08. No theorem verification.',
                                            'suffix': ' [Tropp, An Introduction to Matrix '
                                                      'Concentration Inequalities (2015)]'},
 'claim_b4555901c25042e2813e914e511d2179': {'claim_row_sha256': '7518d2c7dac9e63f51ccebe9d7cec1c86b9c132a841ded54e2d4b98bf47d5695',
                                            'node_row_sha256': '05b1ef675fca3aabe69ced45048ef602605a00b36b1b54ef01391b566421c556',
                                            'review': 'Exact August9 citation projection; '
                                                      'August12/frozen/live row identity checked '
                                                      '2026-09-08. No theorem verification.',
                                            'suffix': ' [standard, e.g. Diestel, Graph Theory]'},
 'claim_b8fd8e1c912a4fe9be63199cb4b60bac': {'claim_row_sha256': '504547eaec2c2613ac9e836bdbc4544d935766e827d229ccc46906e104b54994',
                                            'node_row_sha256': 'caf860b2b035904cab99aa81f7317e6f19467cbefe933178d7a82bdb911ddde8',
                                            'review': 'Exact August9 citation projection; '
                                                      'August12/frozen/live row identity checked '
                                                      '2026-09-08. No theorem verification.',
                                            'suffix': ' [standard, e.g. Horn and Johnson, Matrix '
                                                      'Analysis]'},
 'claim_ba1be536b13349bc9d064747325ea4d0': {'claim_row_sha256': '40fc9c62b488b998070cc4f385f1504084d7a0458adb24874e7160271789d598',
                                            'node_row_sha256': '1ac2468ed19f0df705055c395765ecda530ce1e84a2867d678a8bff0d003c480',
                                            'review': 'Exact August9 citation projection; '
                                                      'August12/frozen/live row identity checked '
                                                      '2026-09-08. No theorem verification.',
                                            'suffix': ' [Erdos and Rado (1960) (1960); see Jukna, '
                                                      'Extremal Combinatorics]'},
 'claim_bb1590c4afe44967a4d2e9acc69bdda9': {'claim_row_sha256': '5516d3b0bb8aa08bb5c4defc31fd8c8a65d02b8d66abef925d671bc9f5cfbb4d',
                                            'node_row_sha256': 'fed486e45dbde93f7732560a2b1f08d2e66ef4e9c9f645300f51b6245ebff066',
                                            'review': 'Exact August9 citation projection; '
                                                      'August12/frozen/live row identity checked '
                                                      '2026-09-08. No theorem verification.',
                                            'suffix': ' [standard, e.g. Levin and Peres, Markov '
                                                      'Chains and Mixing Times]'},
 'claim_bb93b7c549764d11a1d7cd9ee576debe': {'claim_row_sha256': '73934083155b8080b7972e18d9ac37cbde81b05f1be80f7a8f7fcd9bb2383567',
                                            'node_row_sha256': '20a3388d7a497c96faaad7395c22e113b1aa67d680766d4f2f9c526ecde0f9f9',
                                            'review': 'Exact August9 citation projection; '
                                                      'August12/frozen/live row identity checked '
                                                      '2026-09-08. No theorem verification.',
                                            'suffix': ' [Milman (1938); Pettis (1939); see Brezis, '
                                                      'Functional Analysis, Sobolev Spaces and '
                                                      'PDEs]'},
 'claim_bbed6159452a43c6af978a5b0dcb3986': {'claim_row_sha256': '62c7cf26c6ee60950038c20eb86e191052eb2b6153c574fd2da2aa308c734ee5',
                                            'node_row_sha256': '314fb68e8e218ee2e0fc06b4be4ca551fff52ab3528e5e1e7944ea05340ba244',
                                            'review': 'Exact August9 citation projection; '
                                                      'August12/frozen/live row identity checked '
                                                      '2026-09-08. No theorem verification.',
                                            'suffix': ' [standard, e.g. Lieb and Loss, Analysis]'},
 'claim_bea979c6a9cb463eaae64f8ecadf0238': {'claim_row_sha256': '2e69d1f7ab1205b49e12376d108d0a8f97ab834dccf4265eece13fcd9bc816a5',
                                            'node_row_sha256': '03f243a3252fd4a180c6c6ceb4eca5fc4371b60dca88366407cea1a52f8c9fe6',
                                            'review': 'Exact August9 citation projection; '
                                                      'August12/frozen/live row identity checked '
                                                      '2026-09-08. No theorem verification.',
                                            'suffix': ' [Freedman (1975); see Tropp, An '
                                                      'Introduction to Matrix Concentration '
                                                      'Inequalities]'},
 'claim_c0112953a46b4430887cb843c063fa78': {'claim_row_sha256': 'f994b04cb3832cdd8f5e11825905ef743c2788938f5c9eba93fe85025111d7f5',
                                            'node_row_sha256': 'ff8d19c524488eff4f745e0376476ff6cb6caf5b2eda09a57aa0c53058ecacb4',
                                            'review': 'Exact August9 citation projection; '
                                                      'August12/frozen/live row identity checked '
                                                      '2026-09-08. No theorem verification.',
                                            'suffix': ' [standard, e.g. Royden and Fitzpatrick, '
                                                      'Real Analysis]'},
 'claim_c25e0ee671f94bbb808d98e1b866fc9a': {'claim_row_sha256': '3907ff341420cb1b4f5f82e5874f2a23576fada9caca0fe236c439ca57c91662',
                                            'node_row_sha256': '17fb4ae59e4ada9f214ef961f92fab2b584ad3433bcd09e0ec048afacf94c0c1',
                                            'review': 'Exact August9 citation projection; '
                                                      'August12/frozen/live row identity checked '
                                                      '2026-09-08. No theorem verification.',
                                            'suffix': ' [standard, e.g. Levin and Peres, Markov '
                                                      'Chains and Mixing Times]'},
 'claim_c40b8e0024ef46e3896b75668d4b8f17': {'claim_row_sha256': 'd225b296fee1778e55acd98179bd72692b35937e719438eabc728a49c98e76e8',
                                            'node_row_sha256': '1987a45a8ceeea51f2eac8589560491ca41ed79387e42f75579a03e1c2a479cc',
                                            'review': 'Exact August9 citation projection; '
                                                      'August12/frozen/live row identity checked '
                                                      '2026-09-08. No theorem verification.',
                                            'suffix': ' [Prufer (1918); see van Lint and Wilson, A '
                                                      'Course in Combinatorics]'},
 'claim_c87a8e0979104fde898a2a97619f94c1': {'claim_row_sha256': '29b03bd86dc0a8fe46d246ffbffadcdf2d46e732f9e3efbdcc1c2af9a810d7f0',
                                            'node_row_sha256': '4cf18ecc46ddb350592d7a92b3f43291c2fde59835d0765decf86458f681ab4d',
                                            'review': 'Exact August9 citation projection; '
                                                      'August12/frozen/live row identity checked '
                                                      '2026-09-08. No theorem verification.',
                                            'suffix': ' [standard, e.g. Horn and Johnson, Matrix '
                                                      'Analysis]'},
 'claim_c8df104912a240938197f2669f3805c8': {'claim_row_sha256': 'fb527b449e5012ae050d61cffdec4bfb220cbf33d10e9b5af72862723a8e1e28',
                                            'node_row_sha256': 'd6f2a7a08467f36c5a87b41577b85ef516887b07ad2748bb53a3f0f0bd8409bb',
                                            'review': 'Exact August9 citation projection; '
                                                      'August12/frozen/live row identity checked '
                                                      '2026-09-08. No theorem verification.',
                                            'suffix': ' [standard, e.g. Conway, A Course in '
                                                      'Functional Analysis]'},
 'claim_c9acf8bd8773430291e1dcd2c0e9e7e5': {'claim_row_sha256': '693a6e555eda71db78f9d8bc68591783eb91afb9babbf08b45834e8b1011dde7',
                                            'node_row_sha256': 'f8dbe1817fd0cc8551991215624db63fdd63d1ea663f61c1c65db8207beaca2d',
                                            'review': 'Exact August9 citation projection; '
                                                      'August12/frozen/live row identity checked '
                                                      '2026-09-08. No theorem verification.',
                                            'suffix': ' [standard, e.g. Bollobas, Combinatorics]'},
 'claim_cc8898694e8c4d13926f02c45365f9e5': {'claim_row_sha256': 'f561deeaee96a0f8a824e45bbb17db67fba9f4b450a3cbd4d4d3a47aa9776e72',
                                            'node_row_sha256': '8c43a8370483c3e607d2b1120ead64ff330b6ceeeca07538219c10316f5c2998',
                                            'review': 'Exact August9 citation projection; '
                                                      'August12/frozen/live row identity checked '
                                                      '2026-09-08. No theorem verification.',
                                            'suffix': ' [standard, e.g. Stanley, Enumerative '
                                                      'Combinatorics, Volume 1]'},
 'claim_ccde6e9f75bb4fc49ce8119e683e90cd': {'claim_row_sha256': 'ad776c40ff261654c904930c3646180db1556ba235d8be69fd71b69f57fd5e8c',
                                            'node_row_sha256': '2e3731459c126fbc285ced7c11b7f91ac9014bac8fd3bd9504a7d6957cb273be',
                                            'review': 'Exact August9 citation projection; '
                                                      'August12/frozen/live row identity checked '
                                                      '2026-09-08. No theorem verification.',
                                            'suffix': ' [Mirsky (1971); see Stanley, Enumerative '
                                                      'Combinatorics, Volume 1]'},
 'claim_cd526f3e5b2e4bbe9f1ef392d16205ee': {'claim_row_sha256': 'ed8ed88efee1491453194c5e3fc306ad98ac79eeca19d808c846e019430a1abf',
                                            'node_row_sha256': '480cba44265693698995748a7e77b82a558c8e5c2f2def7e95d615761dcc222a',
                                            'review': 'Exact August9 citation projection; '
                                                      'August12/frozen/live row identity checked '
                                                      '2026-09-08. No theorem verification.',
                                            'suffix': ' [Dembo and Zeitouni, Large Deviations '
                                                      'Techniques and Applications]'},
 'claim_cfc44de611dc4501adbd62b15f671e8b': {'claim_row_sha256': '204efd57cf1ee1f6cdb8990d4576725380508c47926a661a0b922c04dcc10305',
                                            'node_row_sha256': '5d74fa053d454aca0fca13d150c3910f3b8822696210d39c492f46a5698314fa',
                                            'review': 'Exact August9 citation projection; '
                                                      'August12/frozen/live row identity checked '
                                                      '2026-09-08. No theorem verification.',
                                            'suffix': ' [standard, e.g. Folland, Real Analysis]'},
 'claim_d42919cf2ac24118a84a6be3f315f5c9': {'claim_row_sha256': '0c23a35adc0d4acacb58e3ebd649f49772dc013406d2ab09488713a8dd69a46a',
                                            'node_row_sha256': '42b9c38269d30b3ba9c723ffbd60bd5a2a315157356af4341634ffac21f943d4',
                                            'review': 'Exact August9 citation projection; '
                                                      'August12/frozen/live row identity checked '
                                                      '2026-09-08. No theorem verification.',
                                            'suffix': ' [standard, e.g. Conway, A Course in '
                                                      'Functional Analysis]'},
 'claim_d4ceae7714f94e59a9363adaaf91efab': {'claim_row_sha256': '1947ae7b8e26146358b6236ae6bbab89010f9fc5d43a34328e8f81c93198125b',
                                            'node_row_sha256': '83fcf78528b5725ca1aa25567a5c0241fc936f2e92ae4ff4be1f64460e9d1bca',
                                            'review': 'Exact August9 citation projection; '
                                                      'August12/frozen/live row identity checked '
                                                      '2026-09-08. No theorem verification.',
                                            'suffix': ' [Strassen (1965); Lindvall, Lectures on '
                                                      'the Coupling Method]'},
 'claim_d56df75f609044dca2777b7f32aa9220': {'claim_row_sha256': 'ef7cf7d3868e68b97aab46a1724adf68085c5aa0fdc7b13956bffdb10abba3ed',
                                            'node_row_sha256': '8f79bbdeb6f402bc059da6980dc92de27941eebd23709a60e17058503b2aa3fd',
                                            'review': 'Exact August9 citation projection; '
                                                      'August12/frozen/live row identity checked '
                                                      '2026-09-08. No theorem verification.',
                                            'suffix': ' [standard, e.g. Stanley, Enumerative '
                                                      'Combinatorics, Volume 1]'},
 'claim_d670ae7ee82f4b8fa13686e50753321a': {'claim_row_sha256': '5811facaacf9eda8d453cccfe079a716696716405a069cccaaa83943fe4ae1a9',
                                            'node_row_sha256': '4c63bfba8033022f36ec46ec72eb8f39c6d6ccf785150379250697891abb0504',
                                            'review': 'Exact August9 citation projection; '
                                                      'August12/frozen/live row identity checked '
                                                      '2026-09-08. No theorem verification.',
                                            'suffix': ' [standard, e.g. Diestel, Graph Theory]'},
 'claim_d80576e85e80446fbbf1e8d83b264e90': {'claim_row_sha256': '2f29af3459149406b6b257b9a73d5e28aa2acfee0d36a5306301da765bbc51bd',
                                            'node_row_sha256': '452e7439f2d95c96e999d5f43b0beec3ce476a3546aa09f4323aa53fd95ff75d',
                                            'review': 'Exact August9 citation projection; '
                                                      'August12/frozen/live row identity checked '
                                                      '2026-09-08. No theorem verification.',
                                            'suffix': ' [standard, e.g. Conway, A Course in '
                                                      'Functional Analysis]'},
 'claim_dc4e850d8bbf4839871bdde3d5663b2c': {'claim_row_sha256': 'bba32867d80bb8e64345d66950ba422da263239ba6f1456cb31ae42798fd98dc',
                                            'node_row_sha256': '219e0035b897b09dd45bfece83fa7a5fcff123dc896f3308556d8d0595d4a9ae',
                                            'review': 'Exact August9 citation projection; '
                                                      'August12/frozen/live row identity checked '
                                                      '2026-09-08. No theorem verification.',
                                            'suffix': ' [standard, e.g. Evans, Partial '
                                                      'Differential Equations]'},
 'claim_df845232c05f4072b1bdbb90a7c5a55f': {'claim_row_sha256': 'f6e61fc28883020f18bdc5a9b0689e4201a05d12c35872180399c2d6efcdc8d4',
                                            'node_row_sha256': '70335e6caa3ddec6feb8871fc954400efe4f1b992b39183fbf4fb2d70dbf4eeb',
                                            'review': 'Exact August9 citation projection; '
                                                      'August12/frozen/live row identity checked '
                                                      '2026-09-08. No theorem verification.',
                                            'suffix': ' [standard, e.g. Stein and Shakarchi, '
                                                      'Fourier Analysis]'},
 'claim_dfd2f208ddc44082af5e7d73f4fe3228': {'claim_row_sha256': '7ab7419b7c369b14e5afd152d17394f3bfe278d04669cf6dbbbd02df82dd6e55',
                                            'node_row_sha256': 'b946acace177fb471eeeb34b7dc58cf1d4b6402505b341b643f43642168f475d',
                                            'review': 'Exact August9 citation projection; '
                                                      'August12/frozen/live row identity checked '
                                                      '2026-09-08. No theorem verification.',
                                            'suffix': ' [standard, e.g. Diestel, Graph Theory]'},
 'claim_dfe29bbf5c4f4e94b965f3160ffefb19': {'claim_row_sha256': 'bfda45f6ce1f94432dc29eca5becd2b7882e6fe939cdd4d55fcd773f4b377baf',
                                            'node_row_sha256': 'c0753a65b52952375c220245c88c1f3f0733226d7dd5224230e6cf6eb1d2f026',
                                            'review': 'Exact August9 citation projection; '
                                                      'August12/frozen/live row identity checked '
                                                      '2026-09-08. No theorem verification.',
                                            'suffix': ' [Durrett, Probability: Theory and '
                                                      'Examples]'},
 'claim_e0e7eefa07cd4cb8968c02e1768b09f8': {'claim_row_sha256': '3a10aaabd415faf63201c189c552b7b25f328fe32d1772efb0c9d21e1659c099',
                                            'node_row_sha256': 'e661a552ef998d3bd163e0ebd33e48f20abb7861fe01bb9f0f02348a358e105e',
                                            'review': 'Exact August9 citation projection; '
                                                      'August12/frozen/live row identity checked '
                                                      '2026-09-08. No theorem verification.',
                                            'suffix': ' [standard, e.g. Durrett, Probability: '
                                                      'Theory and Examples, Appendix]'},
 'claim_e2ac7eec0c264a8588e95fb365adf07f': {'claim_row_sha256': '189b0b057cc92a3e90c94c21304eb88aee783618919ec2947f707e9eeebef384',
                                            'node_row_sha256': 'c5e71795d54d7d985920fc65e8621b38cceafa5b878cc983930f5c4e9be0d97b',
                                            'review': 'Exact August9 citation projection; '
                                                      'August12/frozen/live row identity checked '
                                                      '2026-09-08. No theorem verification.',
                                            'suffix': ' [standard, e.g. Brezis, Functional '
                                                      'Analysis, Sobolev Spaces and PDEs]'},
 'claim_e372b637aa804a6e88bfcbadb94af293': {'claim_row_sha256': '5e44ec0bcec5970918907d861b9d1c248312955307516363a2562e90d42fbbe9',
                                            'node_row_sha256': '37697978d5f75c600f084535b2e65d1cc46c9edcb26d5755523126f752bb923c',
                                            'review': 'Exact August9 citation projection; '
                                                      'August12/frozen/live row identity checked '
                                                      '2026-09-08. No theorem verification.',
                                            'suffix': ' [standard, e.g. Horn and Johnson, Matrix '
                                                      'Analysis]'},
 'claim_e44fc4bc97424a32b8579296df156cde': {'claim_row_sha256': 'f1227197bf7ee03e15e71dbfec5f46a8564752064367fa9ff7ea8b3ff7ca3030',
                                            'node_row_sha256': 'e6ba6e3a0c7c1ff3ed2ddbe8cb396e94fa1805862d328ffe4db658f837988a63',
                                            'review': 'Exact August9 citation projection; '
                                                      'August12/frozen/live row identity checked '
                                                      '2026-09-08. No theorem verification.',
                                            'suffix': ' [Ajtai, Chvatal, Newborn and Szemeredi '
                                                      '(1982); Leighton (1983); see Alon and '
                                                      'Spencer, The Probabilistic Method]'},
 'claim_e476fccb48294427b5d9caa64ebcd096': {'claim_row_sha256': 'd2c160b00f7422f5265f045ecc52712dc1018222b1406295aba2dfa0d231347f',
                                            'node_row_sha256': '74cdb1364536b7c906e931f5ab39503559e1fb7fa3303a4dd44577d9381d1002',
                                            'review': 'Exact August9 citation projection; '
                                                      'August12/frozen/live row identity checked '
                                                      '2026-09-08. No theorem verification.',
                                            'suffix': ' [Bondy and Chvatal (1976); see Diestel, '
                                                      'Graph Theory]'},
 'claim_e8d59b9461b944e083305f1fe9b1800f': {'claim_row_sha256': '48089ae2cb8c4aa4e4685668cccd8b38bde8d9da324e708c63117dcdecb44336',
                                            'node_row_sha256': 'dcde16bc6d4510018327e3563777ab9303d54b64ce1b2a26446e8b49d165f0ef',
                                            'review': 'Exact August9 citation projection; '
                                                      'August12/frozen/live row identity checked '
                                                      '2026-09-08. No theorem verification.',
                                            'suffix': ' [standard, e.g. Horn and Johnson, Matrix '
                                                      'Analysis]'},
 'claim_eb06591304b84dafaa62f24cdb8f4def': {'claim_row_sha256': 'a08cdd8ec07c5c6a86cabb7b44e3d004454c7b30eee21457a52c01722241f783',
                                            'node_row_sha256': '97d3c8054575b270c03c0c7fc890c594afab64b346c91e6305b832533926ad63',
                                            'review': 'Exact August9 citation projection; '
                                                      'August12/frozen/live row identity checked '
                                                      '2026-09-08. No theorem verification.',
                                            'suffix': ' [standard, e.g. Rudin, Real and Complex '
                                                      'Analysis]'},
 'claim_ee84b282d30d40619c7f38585997cf88': {'claim_row_sha256': 'e171028a6d4593ce22d8a9a53b21379792ba0c9a80881d4e3f2e36381039e509',
                                            'node_row_sha256': 'bb4e696f824e2fc271e746eaac2c71ef11e3f040606b925df738d1fec37ca461',
                                            'review': 'Exact August9 citation projection; '
                                                      'August12/frozen/live row identity checked '
                                                      '2026-09-08. No theorem verification.',
                                            'suffix': ' [Durrett, Probability: Theory and '
                                                      'Examples]'},
 'claim_ef2b27452c82488398de2259da959e13': {'claim_row_sha256': 'dff7497211c71ce95d19ddbfaaede9715a25f9c22dddf1a85f15359507fea5d9',
                                            'node_row_sha256': '711a9cf2ba1f14a99cffb0f8d483afa29d69f51b97182aea34ae15a2d79e4c68',
                                            'review': 'Exact August9 citation projection; '
                                                      'August12/frozen/live row identity checked '
                                                      '2026-09-08. No theorem verification.',
                                            'suffix': ' [standard, e.g. Horn and Johnson, Matrix '
                                                      'Analysis]'},
 'claim_f341ac534d8c49648d6d32b71dd73265': {'claim_row_sha256': '9ed9f59c69af6886407cd46ff13d9f5129ec82d85d4c1ca71966fe7c0016fe8f',
                                            'node_row_sha256': 'ab0c704de9a3c1ebedf6930eec396f33ec3f76f8cb01aafa251468ac4540ef94',
                                            'review': 'Exact August9 citation projection; '
                                                      'August12/frozen/live row identity checked '
                                                      '2026-09-08. No theorem verification.',
                                            'suffix': ' [standard, e.g. Rockafellar, Convex '
                                                      'Analysis]'},
 'claim_f521290c2b934841ad51f4e29cd1217d': {'claim_row_sha256': '74b4d36184ec76e615a860c4140b0b89fb6cd72566ebd2e6991765a948027eb7',
                                            'node_row_sha256': '105b7b39284ca9837efcada0ccf7266a2d4adb852b2c98fa1fca71249c6c42f1',
                                            'review': 'Exact August9 citation projection; '
                                                      'August12/frozen/live row identity checked '
                                                      '2026-09-08. No theorem verification.',
                                            'suffix': ' [standard, e.g. Rockafellar, Convex '
                                                      'Analysis]'},
 'claim_f587069c3335472ebb8fc59a43c64f09': {'claim_row_sha256': 'd28d2a167ec5053d58655c47bccf6679ed0811c18843c1b51a79cd5586f202a2',
                                            'node_row_sha256': 'f06038a4b7df4488c701d1eb9618d1893c1375be012b57dd10dfc0c6dc881411',
                                            'review': 'Exact August9 citation projection; '
                                                      'August12/frozen/live row identity checked '
                                                      '2026-09-08. No theorem verification.',
                                            'suffix': ' [Brooks (1941); see Diestel, Graph '
                                                      'Theory]'},
 'claim_f81ccb3a144f483fb76a8ddefc0f63ac': {'claim_row_sha256': '5097cd5084f8b2f8df3f839024b800565bf71b1f751184ef1d7cf060ef83778f',
                                            'node_row_sha256': '81106a39fc6369614420c641136e9a4f65604e52dbc6ee0cececee7b049226c0',
                                            'review': 'Exact August9 citation projection; '
                                                      'August12/frozen/live row identity checked '
                                                      '2026-09-08. No theorem verification.',
                                            'suffix': ' [Jordan and von Neumann (1935); see '
                                                      'Conway, A Course in Functional Analysis]'},
 'claim_fa305107a1ea462b97dfd5ec9852618e': {'claim_row_sha256': '0b893955db4c412b92482e73b3987d17457f40fcd45c6c5989ad90e52a2229ce',
                                            'node_row_sha256': 'd52ca0d2b468a2f344aba27c094ab24ce5f86964363f1775a76e90e5966def6f',
                                            'review': 'Exact August9 citation projection; '
                                                      'August12/frozen/live row identity checked '
                                                      '2026-09-08. No theorem verification.',
                                            'suffix': ' [standard, e.g. Wainwright, '
                                                      'High-Dimensional Statistics]'},
 'claim_fb81dba4db564ad798150c8bec6e7d23': {'claim_row_sha256': '77f97a21f91bbfe5da673f61d403c75dfcabd5c0479322fb5f087cb3a78c8ec6',
                                            'node_row_sha256': '92894c671ab23c8376fee2a8fe77f6c48bfe1207b2a676591f9647621e74a861',
                                            'review': 'Exact August9 citation projection; '
                                                      'August12/frozen/live row identity checked '
                                                      '2026-09-08. No theorem verification.',
                                            'suffix': ' [Durrett, Probability: Theory and '
                                                      'Examples]'},
 'claim_fdc1f5e31b77412bb68efc0e5d3e3a67': {'claim_row_sha256': '0130bdadc92a201564d04711875d7f5503e32bf1da08a36db70bf25ae1f0938b',
                                            'node_row_sha256': 'bba97421d15a5d0c2fc89d97361fee9fef6535ad82ed589aabc1325ae076e826',
                                            'review': 'Exact August9 citation projection; '
                                                      'August12/frozen/live row identity checked '
                                                      '2026-09-08. No theorem verification.',
                                            'suffix': ' [Lindvall, Lectures on the Coupling '
                                                      'Method]'},
 'claim_fde5f7c46c3b42858fe57a153974903d': {'claim_row_sha256': 'f310174b7fc11bfa81aac1e9fae319683ebfc9cc83a08b9632501a2acc4db60c',
                                            'node_row_sha256': '4b884ebd66596b446934f08a790f53f3e4f8f1fea42d71e3a8135353d4d0ebfb',
                                            'review': 'Exact August9 citation projection; '
                                                      'August12/frozen/live row identity checked '
                                                      '2026-09-08. No theorem verification.',
                                            'suffix': ' [standard, e.g. Billingsley, Convergence '
                                                      'of Probability Measures]'},
 'claim_fe3799885e184f8cb9d6d25fbb0345d4': {'claim_row_sha256': '32e3178647b4004ba3fe5ae8bdd1711043bbf57e750c41e360a5fce1044e9543',
                                            'node_row_sha256': 'dbb225779b704a5ce3990d97bac16c35e45e92ad348122ee14ca1b219b11aa5c',
                                            'review': 'Exact August9 citation projection; '
                                                      'August12/frozen/live row identity checked '
                                                      '2026-09-08. No theorem verification.',
                                            'suffix': ' [standard, e.g. Karatzas and Shreve, '
                                                      'Brownian Motion and Stochastic Calculus]'}}


_REVIEWED_LEGACY_TITLE_PROJECTIONS = {'claim_00e3082df042a1f1507f87412ef57d82484cc47c2131cd9dd2925592afddedce': {'claim_row_sha256': 'fa8da9e5d2c7538068f3a461f6af43fa10a348a2634da3f753ef583e034d95b7',
                                                                            'data_source_sha256': 'f89307528db018c986c07902d5e9d80eb9def43f2362fb720db8266f9ca49389',
                                                                            'node_row_sha256': '06018fe33a7f4b5ad33884e7320643a08af38ec88b7577bdb77c6e3ea3ef6771',
                                                                            'review': 'Exact historical '
                                                                                      'writer '
                                                                                      'reconstruction '
                                                                                      'plus '
                                                                                      'live/August29/frozen '
                                                                                      'row equality; no '
                                                                                      'mathematical '
                                                                                      'truth '
                                                                                      'certification.',
                                                                            'writer_sha256': '0870aff37cc959da67fcf7e2e211a95aaef6eb3d5d59ba6282fbb183159bd345'},
 'claim_18624c8e131bd62a8e54d53444db401acb41efde623dbfb5079418ce06c10c53': {'claim_row_sha256': '31fa94b4d78789204c4278f69abb010385af7cf1cf166df728c5025a230f50d9',
                                                                            'data_source_sha256': 'f89307528db018c986c07902d5e9d80eb9def43f2362fb720db8266f9ca49389',
                                                                            'node_row_sha256': '14f5b83df769a28fc280329077b0adf72fb0fc6e8a7e64046d74d5db43ffca81',
                                                                            'review': 'Exact historical '
                                                                                      'writer '
                                                                                      'reconstruction '
                                                                                      'plus '
                                                                                      'live/August29/frozen '
                                                                                      'row equality; no '
                                                                                      'mathematical '
                                                                                      'truth '
                                                                                      'certification.',
                                                                            'writer_sha256': '0870aff37cc959da67fcf7e2e211a95aaef6eb3d5d59ba6282fbb183159bd345'},
 'claim_377bc763f2c462f88449b1f95d9417eb8caf96777cf3e722d9433a3ac61b5aff': {'claim_row_sha256': '1592f513f9002e3b697e4ee3816eaef0ddaa34fe1a9f80d0d6f81499c4af91a6',
                                                                            'data_source_sha256': 'f89307528db018c986c07902d5e9d80eb9def43f2362fb720db8266f9ca49389',
                                                                            'node_row_sha256': '0b1482e79bac764f135c2e5dc141f53442540843e5a4a16f67c5b6aa5b07b43a',
                                                                            'review': 'Exact historical '
                                                                                      'writer '
                                                                                      'reconstruction '
                                                                                      'plus '
                                                                                      'live/August29/frozen '
                                                                                      'row equality; no '
                                                                                      'mathematical '
                                                                                      'truth '
                                                                                      'certification.',
                                                                            'writer_sha256': '0870aff37cc959da67fcf7e2e211a95aaef6eb3d5d59ba6282fbb183159bd345'},
 'claim_397b3867f2f0fbbf4a628b72d6968ba9518ccaa2b3dd08ab98b068f1261ea373': {'claim_row_sha256': 'a2568b53b44f0a7f4649ee17cbb7be7feed21bfcf74f8740643b1110ca1369e7',
                                                                            'data_source_sha256': 'f89307528db018c986c07902d5e9d80eb9def43f2362fb720db8266f9ca49389',
                                                                            'node_row_sha256': '74569830515bc3e6efd4078f7396392b7925d2243acc47932846ffe2b0e51c47',
                                                                            'review': 'Exact historical '
                                                                                      'writer '
                                                                                      'reconstruction '
                                                                                      'plus '
                                                                                      'live/August29/frozen '
                                                                                      'row equality; no '
                                                                                      'mathematical '
                                                                                      'truth '
                                                                                      'certification.',
                                                                            'writer_sha256': '0870aff37cc959da67fcf7e2e211a95aaef6eb3d5d59ba6282fbb183159bd345'},
 'claim_3c9a3bc6343654fd93310550d6e0ffec761a59e354f701b4d5daf8b99a6f98ac': {'claim_row_sha256': '5b91e7cec2002d7890eeb2464c5bb13c51b3178684d001ba8fd7eb389c0c3ecb',
                                                                            'data_source_sha256': 'f89307528db018c986c07902d5e9d80eb9def43f2362fb720db8266f9ca49389',
                                                                            'node_row_sha256': '53eeb1292e3c77f62a5534c830306194e94fc47623e7c32aa1bd446f2cf2d7c2',
                                                                            'review': 'Exact historical '
                                                                                      'writer '
                                                                                      'reconstruction '
                                                                                      'plus '
                                                                                      'live/August29/frozen '
                                                                                      'row equality; no '
                                                                                      'mathematical '
                                                                                      'truth '
                                                                                      'certification.',
                                                                            'writer_sha256': '0870aff37cc959da67fcf7e2e211a95aaef6eb3d5d59ba6282fbb183159bd345'},
 'claim_3ccc6fc493b4e17b5c29966949004f575c51205c8a4f0ba3bc516d0b752ebea4': {'claim_row_sha256': '002ba8880d1b65e986192c10205477c5945835afbb302394fe15bac1f2a50418',
                                                                            'data_source_sha256': 'f89307528db018c986c07902d5e9d80eb9def43f2362fb720db8266f9ca49389',
                                                                            'node_row_sha256': 'deb91585b8cebbef3c3d50bdc1b2b0d76089604845b56ddcf11cc6b83330cffb',
                                                                            'review': 'Exact historical '
                                                                                      'writer '
                                                                                      'reconstruction '
                                                                                      'plus '
                                                                                      'live/August29/frozen '
                                                                                      'row equality; no '
                                                                                      'mathematical '
                                                                                      'truth '
                                                                                      'certification.',
                                                                            'writer_sha256': '0870aff37cc959da67fcf7e2e211a95aaef6eb3d5d59ba6282fbb183159bd345'},
 'claim_41be3350db028a796e58590ede7a39ff0b7b7cfb91588e7d71794db41ce598f3': {'claim_row_sha256': '37c219837c9b22b5d30f9d33794be3b2f95fd078d5942cdae6ac5049b211e2ed',
                                                                            'data_source_sha256': 'f89307528db018c986c07902d5e9d80eb9def43f2362fb720db8266f9ca49389',
                                                                            'node_row_sha256': '5f2408c0ce8263ce6d94ca9b1aa539dff9290091d990bd3b97bae19e15e24c42',
                                                                            'review': 'Exact historical '
                                                                                      'writer '
                                                                                      'reconstruction '
                                                                                      'plus '
                                                                                      'live/August29/frozen '
                                                                                      'row equality; no '
                                                                                      'mathematical '
                                                                                      'truth '
                                                                                      'certification.',
                                                                            'writer_sha256': '0870aff37cc959da67fcf7e2e211a95aaef6eb3d5d59ba6282fbb183159bd345'},
 'claim_4589646d51649c50c19645023defa503b172af43f1e3976ec69a8c2bd1bd9caa': {'claim_row_sha256': '25ee441652e32a50d3d7577b05be920b7c54522ac4e311a5eaf80d551fd7ef05',
                                                                            'data_source_sha256': 'f89307528db018c986c07902d5e9d80eb9def43f2362fb720db8266f9ca49389',
                                                                            'node_row_sha256': 'bdf0cc8795be7dd557962a18f6be891d409ecc7131fd77459ae2d0bc2f982e4b',
                                                                            'review': 'Exact historical '
                                                                                      'writer '
                                                                                      'reconstruction '
                                                                                      'plus '
                                                                                      'live/August29/frozen '
                                                                                      'row equality; no '
                                                                                      'mathematical '
                                                                                      'truth '
                                                                                      'certification.',
                                                                            'writer_sha256': '0870aff37cc959da67fcf7e2e211a95aaef6eb3d5d59ba6282fbb183159bd345'},
 'claim_4ead7f208531d01ecec1d5d0bfd5ad9e6b756fad93bc3a572341869a15853878': {'claim_row_sha256': '7c6d8460103163e3202f1979be2e163a5d08a5d0bbaf02201f4385bbdc9327f7',
                                                                            'data_source_sha256': 'f89307528db018c986c07902d5e9d80eb9def43f2362fb720db8266f9ca49389',
                                                                            'node_row_sha256': '954d7a06980b7f271d8976e277d49241988396a44dd339edd2b9ba0cfed4371b',
                                                                            'review': 'Exact historical '
                                                                                      'writer '
                                                                                      'reconstruction '
                                                                                      'plus '
                                                                                      'live/August29/frozen '
                                                                                      'row equality; no '
                                                                                      'mathematical '
                                                                                      'truth '
                                                                                      'certification.',
                                                                            'writer_sha256': '0870aff37cc959da67fcf7e2e211a95aaef6eb3d5d59ba6282fbb183159bd345'},
 'claim_70377234e16e99d16736ed8a51266efb4d24ec4ed4a9196ad018b77a119f7e71': {'claim_row_sha256': '2fd15eb05b96b51460d0ae8e6ec220bf47b3dbc5bde155aaea26a8d6496e4c9a',
                                                                            'data_source_sha256': 'f89307528db018c986c07902d5e9d80eb9def43f2362fb720db8266f9ca49389',
                                                                            'node_row_sha256': '98faaa843646a10121496fdb03f7eeca5cdb3851b0b9a25b0449e6e1b7ee83f9',
                                                                            'review': 'Exact historical '
                                                                                      'writer '
                                                                                      'reconstruction '
                                                                                      'plus '
                                                                                      'live/August29/frozen '
                                                                                      'row equality; no '
                                                                                      'mathematical '
                                                                                      'truth '
                                                                                      'certification.',
                                                                            'writer_sha256': '0870aff37cc959da67fcf7e2e211a95aaef6eb3d5d59ba6282fbb183159bd345'},
 'claim_74dbfde8ff7726783a6e7e09eb683c369d3e9bc4dffe92abc8a6d00f0e0ab80c': {'claim_row_sha256': 'f9b16e567aaf406db42fd8e7f7ab37286f21fe25f480dcbe965504d64a27c592',
                                                                            'data_source_sha256': 'f89307528db018c986c07902d5e9d80eb9def43f2362fb720db8266f9ca49389',
                                                                            'node_row_sha256': '8355421e26b3d902bf0edfd38169d4be5ab882a035304903ad414b76f1e1cab1',
                                                                            'review': 'Exact historical '
                                                                                      'writer '
                                                                                      'reconstruction '
                                                                                      'plus '
                                                                                      'live/August29/frozen '
                                                                                      'row equality; no '
                                                                                      'mathematical '
                                                                                      'truth '
                                                                                      'certification.',
                                                                            'writer_sha256': '0870aff37cc959da67fcf7e2e211a95aaef6eb3d5d59ba6282fbb183159bd345'},
 'claim_7d89ec88ac106731309c7ad6ae486d38ba159c1513cd7c88f80d7ae78a0a65ca': {'claim_row_sha256': 'e2e5461f26e249d49a2afc09f498814a762197d7447f308f886ca6c92ad33e50',
                                                                            'data_source_sha256': 'f89307528db018c986c07902d5e9d80eb9def43f2362fb720db8266f9ca49389',
                                                                            'node_row_sha256': 'ac081f91b00bea2ca0400e2872265f35011d3efa59100f14932f14d82abf719c',
                                                                            'review': 'Exact historical '
                                                                                      'writer '
                                                                                      'reconstruction '
                                                                                      'plus '
                                                                                      'live/August29/frozen '
                                                                                      'row equality; no '
                                                                                      'mathematical '
                                                                                      'truth '
                                                                                      'certification.',
                                                                            'writer_sha256': '0870aff37cc959da67fcf7e2e211a95aaef6eb3d5d59ba6282fbb183159bd345'},
 'claim_82d8cc27848fcea8a5f9deec83f1bd84499a7c7255201b6e0069b46410df6877': {'claim_row_sha256': 'b89be9f8166fbbe1e874408d11c28a00ed3a8228c983d12cfa6b75b45cd2ae4f',
                                                                            'data_source_sha256': 'f89307528db018c986c07902d5e9d80eb9def43f2362fb720db8266f9ca49389',
                                                                            'node_row_sha256': '5c0eae55aa643aec50b58d99792ce8fcd6888f4afc26aef51be33fca3a5fe98c',
                                                                            'review': 'Exact historical '
                                                                                      'writer '
                                                                                      'reconstruction '
                                                                                      'plus '
                                                                                      'live/August29/frozen '
                                                                                      'row equality; no '
                                                                                      'mathematical '
                                                                                      'truth '
                                                                                      'certification.',
                                                                            'writer_sha256': '0870aff37cc959da67fcf7e2e211a95aaef6eb3d5d59ba6282fbb183159bd345'},
 'claim_8a17e4ab36fa0ae9d777f6fbb2a993eb6ea86c1cf88c8d574bba5a789944a958': {'claim_row_sha256': 'd11247114150266d1ae9ce011d83a52c9a42d9e9ab3510227c67e6c6c5fd0623',
                                                                            'data_source_sha256': 'd7c28c5f9afc89f583758f7a56809d204531ed816daa146085800e31a25ce5e8',
                                                                            'node_row_sha256': '64b6d28e73a573b206196e584e4e7428bd7265fa55abeeb100fb694f2a3db162',
                                                                            'review': 'Exact historical '
                                                                                      'writer '
                                                                                      'reconstruction '
                                                                                      'plus '
                                                                                      'live/August29/frozen '
                                                                                      'row equality; no '
                                                                                      'mathematical '
                                                                                      'truth '
                                                                                      'certification.',
                                                                            'writer_sha256': 'd7c28c5f9afc89f583758f7a56809d204531ed816daa146085800e31a25ce5e8'},
 'claim_9d127862488110bde2375decb6c609004fc6303c37da15ff1c818d0e0f7bed7d': {'claim_row_sha256': '1bf8ce9dfd9679e5103e4bc49789c89230dcf5ec8344ddbdc669cb3f9e1e687a',
                                                                            'data_source_sha256': 'f89307528db018c986c07902d5e9d80eb9def43f2362fb720db8266f9ca49389',
                                                                            'node_row_sha256': 'bf71a2038659f1250ca97a0b859491939dce5dd6e92148132a3f8cfba1a85cf6',
                                                                            'review': 'Exact historical '
                                                                                      'writer '
                                                                                      'reconstruction '
                                                                                      'plus '
                                                                                      'live/August29/frozen '
                                                                                      'row equality; no '
                                                                                      'mathematical '
                                                                                      'truth '
                                                                                      'certification.',
                                                                            'writer_sha256': '0870aff37cc959da67fcf7e2e211a95aaef6eb3d5d59ba6282fbb183159bd345'},
 'claim_9edb634738c5ff2c1335e69a28c8be120a703cb90bf7d30a54bde4f96ad9c1dc': {'claim_row_sha256': '21e35ac6d698f747c7c7d77b6a85f3738cbf4c3fb47ea44e178cf659db967752',
                                                                            'data_source_sha256': 'f89307528db018c986c07902d5e9d80eb9def43f2362fb720db8266f9ca49389',
                                                                            'node_row_sha256': 'f18fb269faf3f8dc7cb8548dab05c6649a85d8b34fda9c1cbe835ece2f77e7f6',
                                                                            'review': 'Exact historical '
                                                                                      'writer '
                                                                                      'reconstruction '
                                                                                      'plus '
                                                                                      'live/August29/frozen '
                                                                                      'row equality; no '
                                                                                      'mathematical '
                                                                                      'truth '
                                                                                      'certification.',
                                                                            'writer_sha256': '0870aff37cc959da67fcf7e2e211a95aaef6eb3d5d59ba6282fbb183159bd345'},
 'claim_b2857167bb318b44793612bac351618eb722bcb5c9b727b295b7901dda772c83': {'claim_row_sha256': '04f2d6944c063ceb46611e953bce49ea956a6f1fbd60c05a2593b21196a760c0',
                                                                            'data_source_sha256': 'f89307528db018c986c07902d5e9d80eb9def43f2362fb720db8266f9ca49389',
                                                                            'node_row_sha256': 'bc038ee53d148916373b98133d0c3a02b7ff2c2fa6df2b9a6ea6decd9a23ac3e',
                                                                            'review': 'Exact historical '
                                                                                      'writer '
                                                                                      'reconstruction '
                                                                                      'plus '
                                                                                      'live/August29/frozen '
                                                                                      'row equality; no '
                                                                                      'mathematical '
                                                                                      'truth '
                                                                                      'certification.',
                                                                            'writer_sha256': '0870aff37cc959da67fcf7e2e211a95aaef6eb3d5d59ba6282fbb183159bd345'},
 'claim_cdf706646c2b45447e641263ec41ed5425512f1b415ae6923ee1a889e83be95c': {'claim_row_sha256': 'c022ee51a05f98e96cfbe52950fad77e77f9e3f03efe3ed9b6502d297a42eb17',
                                                                            'data_source_sha256': 'f89307528db018c986c07902d5e9d80eb9def43f2362fb720db8266f9ca49389',
                                                                            'node_row_sha256': 'cf6089c39214d863598cb6e2d32301b0b77b64928b6192a937ced707b1536219',
                                                                            'review': 'Exact historical '
                                                                                      'writer '
                                                                                      'reconstruction '
                                                                                      'plus '
                                                                                      'live/August29/frozen '
                                                                                      'row equality; no '
                                                                                      'mathematical '
                                                                                      'truth '
                                                                                      'certification.',
                                                                            'writer_sha256': '0870aff37cc959da67fcf7e2e211a95aaef6eb3d5d59ba6282fbb183159bd345'},
 'claim_d7b7749fa43676b4784d2ec0e1fe19614184f5a2e494b7b9b37ada11fc827bbd': {'claim_row_sha256': '70ce82aeae788f9b95476b4565cf3c89a7b1b28204a09ce1d1a59bbb119378c9',
                                                                            'data_source_sha256': 'f89307528db018c986c07902d5e9d80eb9def43f2362fb720db8266f9ca49389',
                                                                            'node_row_sha256': '05b68118f466c46751357699255e82ed01dd62b527c20a2820dc0d7a399c43db',
                                                                            'review': 'Exact historical '
                                                                                      'writer '
                                                                                      'reconstruction '
                                                                                      'plus '
                                                                                      'live/August29/frozen '
                                                                                      'row equality; no '
                                                                                      'mathematical '
                                                                                      'truth '
                                                                                      'certification.',
                                                                            'writer_sha256': '0870aff37cc959da67fcf7e2e211a95aaef6eb3d5d59ba6282fbb183159bd345'},
 'claim_db01a6b5f850cb34880983f32a0ceb78e2137e85e9d771c42ffa78df98b6e7d2': {'claim_row_sha256': '3a8b7318e85ca211b91268270dd44ed739123a0324794cecfae6da5d5222c2bc',
                                                                            'data_source_sha256': '7eb3d1d544847eba160feaf4a81c2c92e0ea745c641b56b673451c23d91dcfcd',
                                                                            'node_row_sha256': '398e54676eca0da386f8307396d0198c2bf19c9db0171f392cf389594017dec9',
                                                                            'review': 'Exact historical '
                                                                                      'writer '
                                                                                      'reconstruction '
                                                                                      'plus '
                                                                                      'live/August29/frozen '
                                                                                      'row equality; no '
                                                                                      'mathematical '
                                                                                      'truth '
                                                                                      'certification.',
                                                                            'writer_sha256': '7eb3d1d544847eba160feaf4a81c2c92e0ea745c641b56b673451c23d91dcfcd'},
 'claim_db1f90d0aa748558c3e988540ece29778e5ea3b63f806730f27be520623afec8': {'claim_row_sha256': 'f9af838da86b4dd626f4df1da0d9621e601a6278a323c6831f967d1b8e2d3f4e',
                                                                            'data_source_sha256': 'd7c28c5f9afc89f583758f7a56809d204531ed816daa146085800e31a25ce5e8',
                                                                            'node_row_sha256': 'ef0d6afadcff1e03a5b6f068cbccb6a69ed2066fdf3389c9ae68e6805e5b7611',
                                                                            'review': 'Exact historical '
                                                                                      'writer '
                                                                                      'reconstruction '
                                                                                      'plus '
                                                                                      'live/August29/frozen '
                                                                                      'row equality; no '
                                                                                      'mathematical '
                                                                                      'truth '
                                                                                      'certification.',
                                                                            'writer_sha256': 'd7c28c5f9afc89f583758f7a56809d204531ed816daa146085800e31a25ce5e8'},
 'claim_e090760039c41cb781c56fbb85679625a58a678ff42cfb3ec11a01d8887de6c5': {'claim_row_sha256': '1ef5bd3fa6e279cec1b5301716e500efe2d6309c23dabdde75d33aecd98efbe5',
                                                                            'data_source_sha256': 'f89307528db018c986c07902d5e9d80eb9def43f2362fb720db8266f9ca49389',
                                                                            'node_row_sha256': '19de1bacaab08c84d10a4bfc50c5bed7fb38c6ef6bd00655b02f0a59a62a4476',
                                                                            'review': 'Exact historical '
                                                                                      'writer '
                                                                                      'reconstruction '
                                                                                      'plus '
                                                                                      'live/August29/frozen '
                                                                                      'row equality; no '
                                                                                      'mathematical '
                                                                                      'truth '
                                                                                      'certification.',
                                                                            'writer_sha256': '0870aff37cc959da67fcf7e2e211a95aaef6eb3d5d59ba6282fbb183159bd345'},
 'claim_e0ac11a71f537e18638ecc1571a2f38ea7228e9554a4f3ee591312e42f2c3f07': {'claim_row_sha256': '232c8144c17a1ca911f7f005b1b572aa987fe3480d21e886dce6dc7b61008954',
                                                                            'data_source_sha256': 'f89307528db018c986c07902d5e9d80eb9def43f2362fb720db8266f9ca49389',
                                                                            'node_row_sha256': 'e7c832c232a98437c1374d0cf9b4406140f1d5c3767ec2578bc2eedad713d61a',
                                                                            'review': 'Exact historical '
                                                                                      'writer '
                                                                                      'reconstruction '
                                                                                      'plus '
                                                                                      'live/August29/frozen '
                                                                                      'row equality; no '
                                                                                      'mathematical '
                                                                                      'truth '
                                                                                      'certification.',
                                                                            'writer_sha256': '0870aff37cc959da67fcf7e2e211a95aaef6eb3d5d59ba6282fbb183159bd345'},
 'claim_f49c854bc72ad92746a0bb6e77eee0ccd68c0adcb5a5458124cd3e427e1bd104': {'claim_row_sha256': 'f95c2f2ee27f753e251a11bfaa56b498550731cd38b691402237eeee75dec1c5',
                                                                            'data_source_sha256': 'f89307528db018c986c07902d5e9d80eb9def43f2362fb720db8266f9ca49389',
                                                                            'node_row_sha256': 'e9d3748c8f76690327cba5847ec0668847333fa7a49b7a030b660ca0acbb4436',
                                                                            'review': 'Exact historical '
                                                                                      'writer '
                                                                                      'reconstruction '
                                                                                      'plus '
                                                                                      'live/August29/frozen '
                                                                                      'row equality; no '
                                                                                      'mathematical '
                                                                                      'truth '
                                                                                      'certification.',
                                                                            'writer_sha256': '0870aff37cc959da67fcf7e2e211a95aaef6eb3d5d59ba6282fbb183159bd345'}}


def _reviewed_legacy_projection(connection, row, node, claim):
    record = _REVIEWED_LEGACY_PROJECTIONS.get(node.id)
    title_layout = record is None
    if title_layout:
        record = _REVIEWED_LEGACY_TITLE_PROJECTIONS.get(node.id)
    if record is None:
        return None
    claim_row = connection.execute('SELECT * FROM claims WHERE id=?', (node.id,)).fetchone()
    if claim_row is None:
        return None
    def pin(value):
        return _sha(json.dumps(dict(value), sort_keys=True, separators=(',', ':'),
                               ensure_ascii=False).encode('utf8'))
    if (pin(row) != record['node_row_sha256']
            or pin(claim_row) != record['claim_row_sha256']
            or claim.id != node.id
            or claim.status.value != 'unverified'):
        return None
    if title_layout:
        if (node.title != claim.statement
                or not node.body.startswith('\n'.join(claim.assumptions) + '\n\n')):
            return None
    elif claim.assumptions or node.body != claim.statement + record['suffix']:
        return None
    return {
        'schema': ('reviewed_legacy_title_assumptions_commentary_v1' if title_layout
                   else 'reviewed_legacy_statement_plus_citation_v1'),
        'registry_record_sha256': pin(record),
        'node_row_sha256': record['node_row_sha256'],
        'claim_row_sha256': record['claim_row_sha256'],
        'interpretation': 'Exact historical serialization only; mathematical truth remains unverified.',
    }


def _json_object(text):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise NoteReadError('duplicate_metadata_key')
            result[key] = value
        return result
    try:
        value = json.loads(text, object_pairs_hook=pairs)
    except (TypeError, json.JSONDecodeError, RecursionError) as exc:
        raise NoteReadError('invalid_metadata_json') from exc
    if not isinstance(value, dict):
        raise NoteReadError('metadata_not_object')
    return value


def _current_id(connection, requested_id):
    """Read the complete connected SUPERSEDES family before choosing a head."""
    wanted, frontier, edges, edge_ids = {requested_id}, {requested_id}, set(), set()
    while frontier:
        node_id = frontier.pop()
        rows = connection.execute(
            "SELECT id,source_id,target_id FROM edges WHERE kind='supersedes' "
            "AND (source_id=? OR target_id=?) ORDER BY id LIMIT ?",
            (node_id, node_id, MAX_FAMILY_EDGES + 1),
        ).fetchall()
        if len(rows) > MAX_FAMILY_EDGES:
            raise NoteReadError('revision_edge_limit')
        for row in rows:
            edge_ids.add(row['id'])
            pair = (row['source_id'], row['target_id'])
            edges.add(pair)
            if len(edges) > MAX_FAMILY_EDGES:
                raise NoteReadError('revision_edge_limit')
            for endpoint in pair:
                if endpoint not in wanted:
                    wanted.add(endpoint)
                    frontier.add(endpoint)
                    if len(wanted) > MAX_FAMILY_NODES:
                        raise NoteReadError('revision_node_limit')
    marks = ','.join('?' for _ in wanted)
    present = {r['id'] for r in connection.execute(
        'SELECT id FROM nodes WHERE id IN (' + marks + ')', tuple(wanted))}
    if present != wanted:
        raise NoteReadError('missing_revision_endpoint')
    indegree = dict.fromkeys(wanted, 0)
    outgoing = defaultdict(set)
    for newer, older in edges:
        outgoing[newer].add(older)
        indegree[older] += 1
    heads = [nid for nid in wanted if indegree[nid] == 0]
    queue, removed = deque(heads), 0
    while queue:
        newer = queue.popleft()
        removed += 1
        for older in outgoing[newer]:
            indegree[older] -= 1
            if indegree[older] == 0:
                queue.append(older)
    if removed != len(wanted):
        raise NoteReadError('cyclic_revision_family')
    if len(heads) != 1:
        raise NoteReadError('ambiguous_current_revision')
    pins = []
    if len(edge_ids) > MAX_FAMILY_EDGES:
        raise NoteReadError('revision_edge_limit')
    for edge_id in sorted(edge_ids):
        sizes = connection.execute(
            'SELECT length(CAST(rationale AS BLOB)),length(CAST(metadata_json AS BLOB)) '
            'FROM edges WHERE id=?', (edge_id,)).fetchone()
        if sizes is None or any(value is None or value > 8192 for value in sizes):
            raise NoteReadError('revision_edge_field_limit')
        row = dict(connection.execute('SELECT * FROM edges WHERE id=?', (edge_id,)).fetchone())
        try:
            edge = GraphEdge(id=row['id'], source_id=row['source_id'], target_id=row['target_id'],
                             kind=EdgeKind(row['kind']), rationale=row['rationale'], weight=row['weight'],
                             metadata=_json_object(row['metadata_json']), created_at=row['created_at'])
            digest = edge.digest()
        except (ValueError, TypeError, KeyError) as exc:
            raise NoteReadError('invalid_revision_edge') from exc
        if digest != row['digest']:
            raise NoteReadError('revision_edge_digest_mismatch')
        pins.append({'id':edge.id, 'source_id':edge.source_id,
                     'target_id':edge.target_id, 'stored_digest':row['digest']})
    graph_sha = _sha(json.dumps(pins, sort_keys=True, separators=(',', ':')).encode('utf8'))
    return heads[0], len(wanted), graph_sha


def _artifact_binding(metadata, artifact_root):
    """A historical acquisition artifact can remain bound after a correction."""
    relative = metadata.get('artifact_path')
    digest = metadata.get('artifact_digest')
    result = {'declared_path': relative, 'declared_digest': digest,
              'verification': 'NOT_REQUESTED',
              'relationship': 'historical acquisition provenance; not necessarily the corrected statement'}
    if artifact_root is None:
        return result
    if not isinstance(relative, str) or not isinstance(digest, str):
        raise NoteReadError('missing_artifact_binding')
    if not re.fullmatch(r'sha256:[0-9a-f]{64}', digest):
        raise NoteReadError('invalid_artifact_digest')
    rel = Path(relative)
    if rel.is_absolute() or rel.drive or '..' in rel.parts:
        raise NoteReadError('unsafe_artifact_path')
    root = Path(artifact_root).resolve(strict=True)
    target = (root / rel).resolve(strict=True)
    if not root.is_dir() or not target.is_relative_to(root) or not target.is_file():
        raise NoteReadError('unsafe_artifact_path')
    before = target.stat()
    if before.st_size > MAX_ARTIFACT_BYTES:
        raise NoteReadError('artifact_byte_limit')
    with target.open('rb') as stream:
        raw = stream.read(MAX_ARTIFACT_BYTES + 1)
    after = target.stat()
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
        raise NoteReadError('artifact_changed_during_read')
    if len(raw) > MAX_ARTIFACT_BYTES or _sha(raw) != digest[7:]:
        raise NoteReadError('artifact_digest_mismatch')
    result.update(verification='HASH_MATCHED', observed_sha256=_sha(raw), bytes=len(raw))
    return result


def read_current_note(database, node_id, *, artifact_root=None):
    """Return one complete current node and optional typed claim from a read snapshot.

    The database supplies text/provenance assertions, not a truth certificate. A body
    too large for the explicit read contract is refused whole rather than excerpted.
    """
    if not isinstance(node_id, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,192}', node_id):
        raise NoteReadError('invalid_node_id')
    opened_at = datetime.now(timezone.utc).isoformat()
    try:
        with ResearchStore.open_read_only(database) as store:
            connection = store.connection
            connection.execute('PRAGMA busy_timeout=1500')
            deadline = time.monotonic() + 3.0
            work = 0
            def progress():
                nonlocal work
                work += 1000
                return int(work > 1000000 or time.monotonic() > deadline)
            connection.set_progress_handler(progress, 1000)
            connection.execute('BEGIN')
            current_id, family_size, graph_sha = _current_id(connection, node_id)
            sizes = connection.execute(
                'SELECT length(CAST(body AS BLOB)),length(CAST(metadata_json AS BLOB)),'
                'length(CAST(title AS BLOB)) FROM nodes WHERE id=?', (current_id,)).fetchone()
            if any(value is None or value > MAX_FIELD_BYTES for value in sizes):
                raise NoteReadError('node_field_byte_limit')
            row = dict(connection.execute('SELECT * FROM nodes WHERE id=?', (current_id,)).fetchone())
            metadata = _json_object(row['metadata_json'])
            if ('statement_fidelity_reviewed' in metadata
                    and type(metadata['statement_fidelity_reviewed']) is not bool):
                raise NoteReadError('invalid_fidelity_status')
            if any(key in metadata and not isinstance(metadata[key], str)
                   for key in ('truth_status', 'trust_class')):
                raise NoteReadError('invalid_trust_status')
            node = GraphNode(id=row['id'], kind=NodeKind(row['kind']), title=row['title'],
                             body=row['body'], metadata=metadata, created_at=row['created_at'])
            body_sha = _sha(node.body.encode('utf8'))
            if row['digest'] == node.digest():
                digest_kind = 'full_graph_node'
            elif row['digest'] == 'sha256:' + body_sha:
                digest_kind = 'legacy_body_only; metadata not covered by stored digest'
            else:
                raise NoteReadError('stored_node_digest_mismatch')
            claim_size = connection.execute(
                'SELECT length(CAST(payload_json AS BLOB)) FROM claims WHERE id=?', (current_id,)).fetchone()
            if claim_size and claim_size[0] > MAX_FIELD_BYTES:
                raise NoteReadError('claim_payload_byte_limit')
            claim = store.get_claim(current_id)
            claim_node_binding = 'no_typed_claim'
            binding_evidence = None
            if claim:
                if claim.statement == node.body:
                    claim_node_binding = 'exact_statement'
                elif _default_claim_node(claim).body == node.body:
                    claim_node_binding = 'current_writer_statement_and_assumptions'
                elif (metadata.get('overlay_schema_version', 1) == 1
                      # 7 Oct 2026: correction cards written on 6 Oct carry the exact overlay-v1 join (statement,
                      # assumptions, conditions) without the version key; the exact join is what the key certifies
                      # (a wrong type is still refused: True == 1 is not accepted below)
                      and type(metadata.get('overlay_schema_version', 1)) is int
                      and isinstance(metadata.get('conditions'), list)
                      and all(isinstance(item, str) for item in metadata['conditions'])
                      and node.body == '\n'.join([claim.statement, *claim.assumptions,
                                                   *metadata['conditions']])):
                    claim_node_binding = 'overlay_v1_statement_assumptions_conditions'
                else:
                    binding_evidence = _reviewed_legacy_projection(connection, row, node, claim)
                    if binding_evidence is None:
                        raise NoteReadError('claim_node_statement_mismatch')
                    claim_node_binding = binding_evidence['schema']
            if claim and metadata.get('truth_status', claim.status.value) != claim.status.value:
                raise NoteReadError('claim_node_status_mismatch')
            artifact = _artifact_binding(metadata, artifact_root)
            result = {
                'schema': 'current-full-math-note-v1', 'requested_id': node_id,
                'current_id': current_id, 'revision_changed': current_id != node_id,
                'revision_family_size': family_size, 'snapshot_opened_at': opened_at,
                'revision_graph_sha256': graph_sha,
                'snapshot_semantics': 'one SQLite read transaction; may precede concurrent later commits',
                'node': asdict(node), 'claim': asdict(claim) if claim else None,
                'claim_node_binding': claim_node_binding,
                'claim_node_binding_evidence': binding_evidence,
                'body_complete': True, 'body_utf8_bytes': len(node.body.encode('utf8')),
                'body_sha256': body_sha, 'stored_node_digest': row['digest'],
                'stored_digest_kind': digest_kind, 'computed_node_digest': node.digest(),
                'source_row_sha256': _sha(json.dumps(row, sort_keys=True, separators=(',', ':'),
                                                   ensure_ascii=False).encode('utf8')),
                'artifact': artifact,
                'trust': {'truth_status': metadata.get('truth_status', claim.status.value if claim else 'unknown'),
                          # what the revision itself declared, when that differs from its authenticated status (B11-R79)
                          **({'declared_status': metadata['declared_status']}
                             if isinstance(metadata.get('declared_status'), str) else {}),
                          'trust_class': metadata.get('trust_class', 'unknown'),
                          'statement_fidelity_reviewed': metadata.get('statement_fidelity_reviewed', False),
                          'independently_verified_by_lookup': False},
                'content_is_untrusted': True,
                'history_policy': 'Only current node/claim text is returned; predecessor texts are withheld.',
            }
            return result
    except NoteReadError:
        raise
    except StoreSchemaError as exc:
        raise NoteReadError('unsupported_store_schema') from exc
    except (OSError, sqlite3.Error, ValueError, TypeError, KeyError) as exc:
        raise NoteReadError('read_failed_or_inconsistent') from exc


def main(argv=None):
    parser = argparse.ArgumentParser(description='Read an exact current math note without excerpting it.')
    parser.add_argument('--database', required=True)
    parser.add_argument('--id', required=True, dest='node_id')
    parser.add_argument('--artifact-root', help='Explicit trusted root for optional bounded artifact hash verification')
    args = parser.parse_args(argv)
    try:
        result = read_current_note(args.database, args.node_id, artifact_root=args.artifact_root)
    except NoteReadError as exc:
        print(json.dumps({'schema': 'current-full-math-note-error-v1', 'reason': str(exc), 'body_returned': False}))
        return 2
    print(json.dumps(result, ensure_ascii=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
