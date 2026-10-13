"""Store fixtures for tests: build a tree and lay a view of it, or seed a
document's result straight into the store without a kernel."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Mapping


def build_view(
    compound: Any,
    *,
    package_dir: Path,
    root_name: str,
    force: bool = False,
    provenance: Mapping[str, Any] | None = None,
    progress: Any | None = None,
) -> dict[str, Any]:
    """Build ``compound``'s tree into the store and lay a view of it at
    ``package_dir`` (the directory shape the older readers consume). Returns the
    build stats plus the tree hash under ``tree``."""
    from cadgen.store.build import build_tree_from_compound
    from cadgen.store.view import export_view

    tree_hash, _tree, stats = build_tree_from_compound(
        compound,
        root_name=root_name,
        force=force,
        progress=progress,
    )
    export_view(tree_hash, Path(package_dir))
    result = dict(stats)
    result["tree"] = tree_hash
    return result


# Proven small box inputs, and each one's part as cadgen derives it under the named
# producer: its display mesh (``glb``) and selector table (``selectors``), zlib-compressed --
# what ``cadgen.store.parts`` derives from ``payload`` under FIXTURE_PRODUCER
# (test_store_seed_inputs.py holds them to it). Keeping encoded inputs here lets catalog and
# security fixtures seed genuine geometry and display data without importing OCP in the process
# under test. A new PART_VERSION, or another producer, names another part: derive them again.
FIXTURE_PRODUCER = {'build123d': '0.11.1', 'ocp': '7.9.3.1', 'cadqueryOcp': '7.9.3.1.1'}
_GEOMETRY_FIXTURES = [{'kind': 'native', 'codec': 'bintools-v4', 'brep': '0bdb4f63f3ab902a54be4da365fe1810a2cf9fb9f688d452e8a608a8c4328f51', 'faceColors': {}, 'contentHash': '01e2a28516540a13ff82f3e0479e481eb16c24c2f60cad2cb2e518d90eecee41', 'payload': 'eJylVttu00AQXTvXuumFFih3DE8Q8QBpxCN1lSLxgFSkRLxbaRsqRXGUEKTyxHfwE/wAUvsX/EbEFzBOdmYTeyZrB6uKVzuzc87MnB3XOx2eD/zWcbt1fPLe70TDqB/1rvzPzVf+i+5Lf24Nx93w7Nz7GHXDr5fRYOy/9lqT0bfzxtnYbzQ9R7HP9AhXWR2qgeB4LUQwkZION+tyECHyc7BBoCF/HcwJG4ebdTlIzxocsjpkr0PuUtsjBP/NIRVhdkfG/puGdEOys1MJMj+SWAKZ3Aj0SAhS20WE5AFrDrSBjnkR6JEQ5IjJAJY+2A8EeRECG8KnqH/ViwaHJzCF9fp00BldhoPepE/zuT0ZXYRdUN9bm/jSCNn8pte4yi2l1QC5u5A9kETIdntSB/IC5x4AUiCJkHUoC4EsfqbJKYV5nfaXcAgKO2x6lQ+/fv/8++f7Oz5q3ChHOfBXt3jOU5l5ltHTmW3wcV1HSfoDowsvdxWj+PBz3Hum3yLHIJXNEkceSQVuQeQIxiL8FgQjcXyKW0jWxlEpniOPBKUqiRzBWIZXUTCmOT5GjkvI/HkoQEVEBmMVfkuCkZAR0HSwNE9eOb7ecZ6gzyNcPETnoiKeFNyZK+4B+iTKzX3TeIFzF5JrDJ8k1H4DXmXJ6KkMAr+Hewe2bIJUNksceRrQpU2RIxhrKoPA7+AWkrVxjB+OY4UFglJtiRzBuK0yCJw47iPHJeSqkKO7IyKDcVdlEDgCmg6SwO/qHec2+uzh4hY6G4EbGlrgu2wqGyyf6ZEbh+T1BsY9MZW4eToVzMDc1SVkTwq+vwo5zp2X2AIyXQOaoVTEbb1D1VRbuPDR2RTR0NBFrLGpbEpsYx3xYgNjzMBaRNIDP2prwnn3YBVyXB9rEekO4LQ3RcTWOSRAkhHO3IUiGhq6iHh9TEAc0EYUqGiFDVsIaIqmA6LWTUD8Bhh906XBfi8ENLXQAZFyvYAB76PHDnpS03EOmcGO8eou/jeDQqrT9+8fa241eQ==', 'glb': 'eNq1Vktv2kAQXvIOSdu0TdOeqmhPPTjIL2zMpaS0lVIlECVRLhGHxV5Tq2AjY/JQhMQ/67U/q7P2GhbzCI1UJO/ON5755rE7QKt99X0FIfQpj9DXdYR+XNZrj5j0ejTC5Ud8S8OeF/i4jNWCjCXcoj4NSRSEoLGJAxAPJEzvo5D0mD3XgdQlITBgR3FLhm5QWTc0jaiW6Zg2UWS1qJWKsmFTSyFEsWSrZDtELxqqaVtN2TRsi8hNlSj06FaDsM2g7ztxgI4H7DdyQZb405Bwh9yDTgEMSUoa6CAnl9i0GrSDENxuwIhCYtU2K4wpsB/4FIhdSqJ+yKSI+JB5BFKPkg5sDuXF0jQBEj6ACJ5nxPfcoO0A6vu//ODOx40kYpxjs++6NLz26B0uaxK2wRdaYQySJKZM9JGJonKb88DzoynD4shQ1QcDMO3ZkCEuy1xihT1Cfk4sQRca0ghB52jvZ2wbBhGJ4kO9OZILpmwqsmGWFKVkFHXTkMa9zb5qsDikTZMDkBVJWONgLASP1g29jhd5txySKAq9Zj9Kqj+vX55cndRrcTq1+sXZ8SkUDwV5vuPFTVSBDDKH5gxiZmKDupcc5mRTZNaUTheO04+uHrrgUlRUQ+gUHG2sxtffquwuLXuDJsMo/xYm667Och/fDc0YuV9Wj0+PLzArekwglh2X3HyIaN114ynl8JT6rQhOWC2V2G0OW+ydpluGOk5myjc2frZ3Ea7LhLepTjprC5wNPRPaAjKfdOjoq6WQTNQCDlPXJzkUS82SJDO3gMTSjBk9mCbhQzk+GX4qYnhVZRf28PDwzxpCX05qaPanUkkFUZnoh5/HqlQe6SuCviLwZOyzsUQeMa7IOZXDDP4pXzSHJxMrm4PIP/zNCDjPMnIad7icvDRvKg+fEWOY8V8kozn9mynnEPt1zqFV2NfRGtqAdQNtoh20hbZhzcP+Cr1Au7DuopfoLdpDr2F9A/t79A7tw7qPDoAh+WzwfYXLKV6FZ1fAcH2BY4zhrwH6IGC2fxRwTuDN8X1FwKucM8Vr3DfFjH9LwOxdXsCbPL8UM9uXAt6GZ0/AeZ5/infg2Rcw4zoQ8BNzOuM+z5vfpecFPTEXc+b9qbn7P98DfwGojvF4', 'selectors': 'eNqtWFuP6jYQ/i95hlXskAD7enpRVamVeo7OywpV3jCA25AgJ6S7OuK/d2Zi5w5kxWqVLInj8ffN3f7h5fEBjuo7mFxnqfcsZt7JZDudgPfsKVPonYoLb+ZBWpj333W6xdcnfI+vYnVSrzrRhYbce/7hwXYPXxKV53qnY1WwuB+eSvaZ0cXhiBOzeP5q4DSPs7TQ6VkX7/NSoCSV7s+JMt+yBIxKY/gJ9t6znHm5Op4SEh5c8OFsEAv8jKv8BekWjE73/QUIwjzRKeSV4FLnmiG+MzAS9eLtQBVnAzhc4MJIDH/loI74bwv4jBgK8Da4JOJMIS5g+xUS/G8ZMc+D0pae3qdHFMKYgyf/Uov9BdHigoU5wwXfvr5mbzTjSBNf/Cd/Zq/NzDsqHHoR+CzxQim0el6oghWbxfHZGEDFfMnOCJeMlIDa/TnyPj+oU+uRNGafolmF3I6hekswBbzZF6tLNd7R7SP6G2ifF2JCTgKiuLC+XhMYME3Ox5TX01uUfFLFAf+l6kgL59nZxPBH9YDuiCB+o68KdJ98lxnCwvq2CvlaVC7b0k6lGzfQ6KnSgnvfaMypy420lbepJXdBJ1msEgbWEKsezVanKsFf/1JIjfOyBGIkB4aixICihWkR+DD+jftoIkQm1ANrQ/Db+wkaPDW+FPXOn1ng1/RoMJhKCnPCnah9XtlQHfPGpX412flEmKuJD2DGD0qHOIF0z25UY7ZQr6nyusnv0FDbf1Rsc4Cb0GQ9ihR9gK1RCaWNQaTQx5xgNs2ij6igx/aKg1zawmipFy+jJIpXek6S1o0zld+7PvBuM5ueBnECXhH9l3hbbVywOYRPOYHkG8GlHJglpKQG7/TV8JOQUYuncDOTNBDRBAtgY6OoXntHy+7c2g4NFdFEpegZNPnFaYJlvszp57yGcQub39PEYhYRFsyTRu9HZqk3nVtKvl0E5wlclLFKwip7WGUXq+hg7UiqoYq7alyMQBUToEoHNSCoQQ9qUEMNKrWG1qEIKksRA6zXTO7XWFeENpyg1nnLo3Fe4LAuCOuih3UxxCobF2hL6kCVN9WKETCOVd7BunBYQ8Ia9rCGNVbpsNbgKklzp91pkWv9LkK0wWqCZtup4TILHdiIwEY9sNE42IDB3swxwVW9Sv8G0uA60mhjK1SdD4CQwkg+oKa09gW/8dt74e+3wl/ytQhm0eK6SrfauFa1h7ZuqLd/H5TBBLmmsaadc3kCKEXASJ6oOMh2SgvuKbqbwqT9C5ZXOAQDDj1PvkfCZRCg5AEjGaRnCDlmCHkzDy/ICNcMIT/BEC6zACUVGMksI4aYXEsqNUYIIrplCP9RQ7iUA5RtYCTltAwhxiJCTMjckq+BIcQnRYTLREBJCEYyUcsQYiwixN3UIyik+TYwhfismIgcjSXRWPZoLAemkGOmuFuZZHUbNcZnRMXSsVgRi1WPxWrUGJPbFluxKKz5dtUYD8fFytFYE411j8ba0RDtDmdyR1MXXlnd5GpyePc7sTs01o6G8Lni+f2S548yCaYU5ibCBYU3364yCR5lIvyaSlW8B9VbDKjIj/RuvusxpL2tJteNj1KpS7jgGi76RVzIUSrBGJUb7RIXcXmLyuNW4b0WbX51a7tVHeoN26uuN9zrR9o9lau1JamqHDY93YiZ0q3Z3UEjmZqQctiJdHV1S/vd9iNqJFNrUA77g65DTWlsKszrRjLV63JYtLs16V6V89vloRZNZbQc1tJuhp3SBNhkFzaiqbSVw/rWcd9BUbtRm1eNaKo35bDodKvblHpp3V+g6A0fIvFJeXUs6o63s/94r4GOjXpDg2IxQuXTRnVNxxDcMghyBPq9xAGBAyGHP29H6NSpFkK7BtoyBnyAQCIFfUuT61Y25GUCanhYwHc+dLIi6PyFGxSeih9V23uLasnieAOFvyU1G/bMqkdlzRhWFYKKmaBpIY4smEWIApnXkpLy5fI/nqyjvA=='}, {'kind': 'native', 'codec': 'bintools-v4', 'brep': '92e6935c58872e596eeda474ad1f8e7dcc4d0c75f4ad9565e332ffc38683671f', 'faceColors': {}, 'contentHash': 'ed085edcb64085e841fec251451ac621ec0789496dc57f816d1eadc03b56df33', 'payload': 'eJylVstu00AUHTvPuumDFihvDCuIWEAasQRXKRILpCIlYm+laagUxVFCkMqK7+An+AGk8hf8RsUXcJ34zMT23My4WFE8mrlzzrl3ztzEO5kMxn7nqNs5On7n96JJNIqGF/6n9gv/Wf+5v1wNZ/3wdOB9iPrhl/NoPPNfep359OugdTrzW23PEdrn6i1GtgH1gAm8ZBAUUjbg93U1sBTFNRgpAibAXAcuoHgdOA3ccw0NtgH2dSheaiNC8N8acgiLOzLzX7W4G2KvTmTEfM9yMWIKM8iHZTAg5xkyG8w5BJl3UQb5sAwsYhbAcA7mDRlp1ht4ho/R6GIYjQ+PqQsn45Nxb3oejofzkezP3fn0LOyT+16bzJdnsIu7usSosJXWExQ+BXsgTpDp9uQ2FCUu3AA4IE6QsSkzQIY4dcg5h3m97udwQg47bHu19z9//fj759sbPWp8UI5w6NM0RC5TWURWEeksJvS4riM4/9GiSy93naJ481PMPUnerMYgl01Ko55JBG6J1UiLZfouMYtS42NMQaxJoxB6jXomKlWF1UiLVXqVmcW8xofQmGLW76cC1FhmWqzTd4VZlMwgVCdYWSYvHD+ZcR4h5gEG9xFcFlKnBHeWjruHmGy5g7SWNQbPRC7ANQejT5Jqv0GvKrfoCQuD38HcgSmbIJdNSqNeBp3SJquRFhvCwuC3MAWxJo3xo9NY0xJRqbZYjbS4LSwMLjXuQ2OKuc7k6O6wzLS4KywMDkJ1gtLgt5MZ5yZi9jC4gWBlcCUjMfiuNpUNTm0Myba5PZtUkIG6qylmjwPfX8cc5260mLwGsofKIm4nM7KaYgsDH8GqiEpGUsSGNpVNTm3sI7Ybxgrs/aBvtQ3tdgI/WMcc18f+nqLbqyLi6BxpQGkj9NyVIioZSRFxfRQgGrQyBRwtcGArgKpoCSC8rgDxG6D8LS8NznsFUNUiAYTkZgmAdxGxg0h56OhDqrEDr+ni3wyM1JS/f/8AfxceIg==', 'glb': 'eNq1Vltv2jAUNr2Xdlu3dd2epspPe0hR4oRAeBkd26ROLVRt1ZeKB5M4LBokKIReVCHxz/a6n7XjxIAJl7JKQ7J9Pvuc71zsAzRbV99XEEKfsgh9XUfox2Wt+ohpt8siXHrEtyzseoGPS5jkVKzgJvNZSKMghB2bOgBxX8HsPgppl+uLPZA6NAQGrNm6wyw3bxEjT/NGgRHLtLWGywqubjPDIcW84+gusZnKig21QS3CXLuo2Y5j53XLPrrVwW0j6PlO7KDtAfuNmlMVMeoKbtN72IMAFT502IOYXGqzStAKQjC7ASUGgVVaPDG+gf3AZ0DsMhr1Qi5F1IfII5C6jLZhcZhIlg0DoOEDiGB5Rn3PDVoOoJ7/yw/ufFxPPMYxNnquy8Jrj93hkq5gG2yhFGY/CWJKxRipaETonAeeH00p5keKxOj3QbVrQ4S4pAqJJ/YI8TmxBFWoKyMElWPdn7FuGEQ0ii/15kjNFdSCppqFoqYVTbgfUxnXNn1U535oiyUXoGqKNMfOuAvhrRN6bS/ybgWkURR6jV6UZH9euzy5OqlV43CqtYuz41NIHhLyfMeLi0iADCKH4vRjZmrDdje5zMmiqLwo7Q5cpx9dPXTAJK8RU6oUXG28ja+/VfhbWvYFTbrR/s1N2pzMMh+/Dd0cmV9Wjk+PLzBPekwgpx2n3HiIWM114y4V8JT5zQhumBSL/DWHTX6mG5ZJxsFM2cbKz7bOw3OZsC6QSWN9gbFppFxbQObTNht9teSSjlrAUTCMSQ7NImmSpOcWkFi6OaMG0ySiKcc3I25Fdk8If7CHh4d/1hD6clJFsz/l8lCQN8V+am+kL5+VJX00rT/lqzzHb3lBDLP407ZoDk/K11QMEv/gN8iD5Gwp+bPgHSwnL807lAfP8DFI2S+SUSqPhXIG8V/nDFqFdR2toQ2YN9Am2kFbaBvmLKyv0Au0C/Mueoneoj30GuY3sL5H79A+zPvoABiSz4ZYV4Q8xKswdiUMzxc4xhj+GqAPEubrRwlnJN6MWFckvCo4h3hN2A4x59+SMD/LSnhTxDfEXPelhLdh7Ek4K+If4h0Y+xLmXAcSfqJPZ73nef27bL+gxX0xt9+f6Lv/8z3wFxu554g=', 'selectors': 'eNqtWFuPqzYQ/i88JyswgST7enpRVamVeo7OSxRVXpgkbglEhtBdHeW/d2aMuSchymoFWTCe+b4Zz8X+4eTRAY7yO+hcZanz6s2ck852KgHn1ZG6UDsZFc7MgbTQH7+rNMbXJ3yPryJ5km8qUYWC3Hn94UC8hy+JzHO1U5EsWNwPRyb7TKvicMSJWTR/03CaR1laqPSsio956aEkme7PidTfsgS0TCP4CfbOq5g5uTyeEhLuX/DhrBEL/Ixa/oI0Bq3SfV8BQZgnKoXcCC5VrhjiBwMjURtnB7I4a8DhAhUjMfwvB3nEnxjwGTEU4GxRJeJMISog/goJ/laMmOdBqoqe2qdHFMKY/Rf3Uov9BdGiwkKf4YJv396yd5pxpIkb98WdVdd25hwlDm0EPtOFUkh7XsiCDZtF0VlrQMN8yc4Il5yUgNz9OfI+P8hT65EsVj2FM4O8GkPzlqALeK9erC5mvGPbZ+w3sD4rYkJWAqK4sL3eEhgwTc7HlPWpGCWfZHHAn1QeSXGenXUEf5gHXI4I4jf6qsDlk+8yTVjY3pVBvhZmybasY2xjBxo7GSvY943FrLnsSNt421pyF3SSRTJhYA0x86hjlcoE//uXQmqcV0UgQnKgKUo0SFJMSuBh/Fv70USITKgHtgrBbx8naPDU+FK0O39WAb9mR43BVFKYE+5E7nPjQ3nMmyX1q87OJ8JsJj6BGT8oLeIE0j0voxpzBfWaKa+7/A4NGf8joyoH2AlN1qNIUQeItUwobQwihT7mBLNtlD5jgh7bKwvk0hZGqjZORkkUr/ScJK3bxmuyV3098G47m54GcQJeIf0KvK22NtgswpecQPKN4FIOzBIyUoN3ujZmZq5gO/NpwKObRbCtwqhWviO9O6vcwqEqmsgUl0aIkzduW+hmTv/Oaxy3wLk9UyxmAVkDE6VW+5FZ8l0RMmt2UoLzPFTKWAVhFT2sootVdLB2JNVQxV07LkagiglQhYXqE1S/B9XvQvXqFUVQWYo3wHrN526NdTXVrPPWksZ5vsW6IKyLHtbFEKtolkBbUgequGlWDIFxrOIO1oXFGhDWoIc1qLEuLNYanJE0t9adFrrVugtpHfgTLNvODZdZYMGGBDbsgQ3HwfoM9maS8a/aVbg3kPrXkYbbqkTV+QAIKYzkA+pKHZLVBhncD3+3Ff6Cr4U3CxfXTRorbXvVHtq6o47/PkiNGXJNY00/Z/MEUIqAkTxhOIh2SvPvGbqbwkT15wdXOPgDDr2VfI+EzSBAyQNGMkjPEWLMEeJmHl6QE645QnyCI2xmAUoqMJJZRhwxuZYYM4YIIrzlCPdZR9iUA5RtYCTltBwhxiJCTMjcgq+BI8QnRYTNREBJCEYyUcsRYiwixN3U41FI823gCvFZMRFaGkuisezRWA5cIcZccbcyCXMbdcZnRMXSslgRi1WPxWrUGZPblqpiUVjz7aozno6LlaWxJhrrHo11h4bXVjqlo6kLrzC3B8K734ndobG2NDyXK57bL3nuKBN/SmFuItyj8ObbAxXjQSaeW1MxxXtQvb0BFfFI7+baHkOY2ygV8RlO8eoS7nEN9/pF3BOjVPwxKjfaJS7i4haV573Cey3a/arWdsuc6g3bq+5quNePtHsqW2tLMlU5bHq6ETOlW6t2B41kakLKYSfStdUt63fbj7CRTK1BOewPugtqSmNjMK8byVSvy2HR7take1XObZeHWjSV0XJYS7sZdkoTUCW7oBFNpa0c1rfO8h0UtRu1edWIpnpTDotOt7pNqZfV8vdQ9JZPkfio3JyL2vPt7D/ea+DCRruhQ5don4A3qms6huCWwaOFQP8vccDDgYDDn7cjdOxUC6FdQ0ArnQ8QSKRH39LkupUNWI1PDQ8L+M6nTpUI2m9y3uKp+BFv78MK1ZLFuTx9SV1lfWjVo7JmDCuDwDDz+EgDRxbMApOJ4bWkpHy5/A8IyqPZ'}, {'kind': 'native', 'codec': 'bintools-v4', 'brep': '7616b493c09ff73f1a25c48badeafa1330aa0fbc821a6df7f88370fa68fa1915', 'faceColors': {}, 'contentHash': 'bca29d8e668cef75b9fa10687b2f4b3eb1535e5c4b11619bd55255a24e503011', 'payload': 'eJylVstu00AUHTvPuumDFihvDCuIWEAasQRXKRILpCIlYm+laagUxVFCkMqK7+An+AGk8hf8RsUXcJ34zMT23My4WFU8mrlzz7lnzlzXO5kMxn7nqNs5On7n96JJNIqGF/6n9gv/Wf+5v1wNZ/3wdOB9iPrhl/NoPPNfep359OugdTrzW23PEdrn6i1GtgH1gAm8ZDKoTNmA39flwEIU52CCQLnFdWADCuvAcuCea3CwDbDXobDU5gzBf3PIZVjckZn/qsXdEHt2IkPmexaLIVMYQT4cAqcHi5DdYKxBBhiQWAT5cAh8xmwCwzmYN2SoWW/gET5Go4thND48pi6cjE/Gvel5OB7OR7I/d+fTs7BP7nttMl8ewS7u6hKjwlZaD1D4FOwTcYRMtye3oShw4QbAJeIIGbsVk8gQpw455zCv1/0cTshhh22v9v7nrx9//3x7o88as3KEQ39NQ+SylEVkFZHOYkKf13UE5z9adOnlrmMUb36KuSfJm+UY5KpJcdQjicAtsRxpsUy/JWZRcnyMKZA1cRRCz1GPRFJVWI60WKVXmVnMc3wIjilk/X4SoMYi02KdfivMokQGoDrByrJ44fjJjPMIMQ8wuI/gspA8ZXJn6bh7iMnIrfum6Q2uu5C6g9EXSdpv0KvKLXrCwuB3MHdgqibIVZPiqKdBp7TJcqTFhrAw+C1MgayJY/zoONa0QCTVFsuRFreFhcElx31wTCHXmRrdHRaZFneFhcEBqE5QGvx2MuPcRMweBjcQrAyuaCQG39WWsqHlQzrFKTm/uXtsKSsiogJ1V1PIHpd8fx1yXDvX2fLXQPZQKeJ2MiPVFFsY+AhWIioaiYgNbSmbHNvYR5zZ3JiBUUTpB32rbTD73YN1yLE+RhHlHUC3VyLi6BxpQGkj9NwVERWNRERcH5UQDVqZAo4WOLCVhEq0JCG8rhLiG6D8LS8NznslodIiSQjKzRIS3kXEDiLloaMPqcaOfE0X/83ASE35/fsHBFke6g==', 'glb': 'eNq1Vktv2kAQXvIOSdu0TdOeqmhPPTjIbx6XktJWSpWEKIlyiTis7TW1CjayTR6KkPhnvfZnddZeYLEJoZGK5N35xjPfPHYHaHcuvy8hhD4VEfq6itCPi+bpAyZRRGNce8A3NIy8wMc1rJZkLOE29WlI4iAEjU0cgHggYXoXhyRi9lwHUo+EwIA1xzINw7ENgyi6ajmuaToVs+JWXUMHFZFNhRLbIZZMqzbRHFVxK7JOjIpiqWXbNg5uNAhrBX3fSQJ0PWC/lkuyxJ+WhLvkDnQaYEhSgr0FObnEpo2gE4Tgdg1GFBJrdFhhTIH9wKdA7FIS90MmxcSHzGOQIkq6sDmUF0tHCZDwHkTwPCG+5wYdB1Df/+UHtz5upRGTHK2+69LwyqO3uKZJ2AZfaIU5SJPImehjE0XlNmeB58c5Q2NsqOqDAZhGNmSIazKXWGEPkJ+TSNCFljRG0Dka/UxswyAmcXKo1wdyqSyXFdksVxSlYhp62ZQmvc2+arE4pEPTA5AVSViTYCwEj9YLva4XezcckjgOPasfp9WfNS+OLo+ap0k6p83zk8NjKB4K8nzHS5qoAhlkDs0ZJMzEBnWUHuZ0U2TWlG4PjtOPL+974GIoqil0Co42UeOrbw12lxa9QdNhlH8Lk3VXZ7lP7oZmjt0vGofHh+eYFT0hEMtOSrbuY9p03WRKOTymfjuGE1YrFXabwzZ7p+lVU50kk/NNjJ/tbcB1mfIuq9PO2hxnU8+ErgKZT7p0/NVSSidqDkdZ16c5lKqaJUlnbg5JVTNn9CBPwodycjL8VMTwqsou7P7+/p8VhL4cnaLZn3p9JIjKVF+v5+3GegGjjO0Ub0Yn8ohxRc5cDvXZfKiej5XjycTK5iDyD3+DPEzfLSR/5rzDxeSFeUfy8Bkxhhn/eTLK1DFXLiD261xAy7CvohW0BusaWkdbaANtwlqE/RV6gbZh3UYv0Vu0g17D+gb29+gd2oV1F+0BQ/pZ4/sSl0d4GZ5tAcP1BY4Jhr8G6IOA2f5RwAWBt8D3JQEvc84RXuG+I8z4NwTM3hUFvM7zG2Fm+1LAm/DsCLjI8x/hLXh2Bcy49gT8xJzOuM+Pze/C84Lmz8Vj8/7U3P2f74G/oAjs3g==', 'selectors': 'eNqtWFuPqkgQ/i884wS6BXVez16y2WQ32XNyXozZ9GCpvYtgGmRncuJ/36qC5q5inBhAaLr6++pO/3Cy6ABH9R1MptPEefVd52TSnY7BeXWUyfVORbnjOpDk5uN3nWzx8Qmf46NIndSbjnWuIXNefziw3cOXWGWZ3ulI5Szuh6PifWp0fjjixDSavRk4zaI0yXVy1vnHrPBRkkr251iZb2kMRiUR/AR751W4TqaOp5iEywvenA1igZ9xlb8g2YLRyb6/AEGYxTqBrBRc6EwzxA8GRqLWzg5UfjaAwzkujMTwXwbqiJct4D1iyMHZ4JKIM4Eoh+1XiPFaMWKeB6UrenqfHFEIY5Yv3qUW+wuixQVzc4YLPn17S99pxpEmrr0Xz62OjescFQ6tcbYr8MArrZ7lKmfFplF0NgZQMV/SM8IlI8Wgdn+OPM8O6tS6JY1Vd6FbIq/GUL0FmBzeqwfLSzne0e0z+htonxdiQlYCoriwvt5iGDCNz8eE19NblHxS+QEviTrSwll6NhH8Ud6gOyKI3+itHN0n26WGsLC+K4V8zUuXbWmn1I0daPRUasE+bzRm1WVH2srb1JK7oOM0UjEDa4iVt2arExXjv38ppMZ5VQQiJAeGosSAooVpEXgY/8a+NBEiE+qBrULw28cJGjw1vgT1zq9VwK/p0WAwFRTmhDtW+6y0oTpmjUv9atLziTCXE5/AjC8UFnEMyZ7dqMZcQb2myusmv0NDbf9RUZUD7IQm61Gk6ANsjYopbQwihV7mBLNpFn1GBT22Vxzk0hZGS62dlJIoHsk5jluntd9kr/p44NnGnZ4GcQIeIV0FnpYbG2wW4UtGIPlEcCkHpjEpqcE7fTVkFjBqvG7cOQ34S5pRIdhUYVQvvqN1d3ZxC4eqaKwSdI0QJ6+tKljoekZ/ZzWOW+C8nirm7pxwYKI0ej8yS71rQmbVTovgPB8XZayCsIoeVtHFKjtYO5JqqPKuHucjUOUEqMJClQRV9qDKGuqKoJKtvBoqS/EHWK/Z3KuxLhFrMJ+g1lnLpXGetFjnhHXewzofYhWNC7QldaCKm2rFEBjHKu5gnVusAWENeliDrgvYGGCBpaSZ1e600K38LpzqsO3ccHEDCzYksGEPbDgOVjLYm0lGXtWruBVa8jrScFOVqDofACGFkXxAXalDstogg/vh77XCX/AhV244v67SrTa2V+2hrTvq7d8HZTBDrmis6edsngBKETCSJ0oOop3S5D1Fd1OYqH5SXuEgBxx6nnyPhM0gQMkDRjJIzxBizBDiZh6eu4KPUUOITzCEzSxASQVGMsuIISbXklKNIYIIbxnCe9YQNuUAZRsYSTktQ8ixiJATMrfgY2AI+UkRYTMRUBKCkUzUMoQciwh5N/X4FNJ8GphCflZMhJbGgmgsejQWA1OIMVPcrUyiPI0a4zOiYmFZLInFssdiOWqMyW1LVbEorPl01RhPx8XS0lgRjVWPxqpjDNvhTO5o6sIrytMDtaLfid2hsbI0fI8rntcved4oEzmlMDcR7lN4+8tbTOSzTHyvplIW70H19gdUxCO9m2d7DFGeHqgbj1KpS7jPNdzvF3FfjFKRY1RutEtcxMUtKs9bhb+16OtXtz63yl29YXvV9YZ7/Ui7p7K1tiBVFcOmpxsxU7q16uugkUxNSDHsRLq6uqX9bvsRNpKpNSiG/UHXoaY0NiXmVSOZ6nUxLNrdmnSvynnt8lCLpjJaDGtpN8NOaQKqZBc0oqm0FcP61nHfQVG7UZuXjWiqN8Ww6HSr25R6Wbm/j6I3vIvEW+Xlvqjd307/428NdGzUGxp0gfoJ+EN1RdsQ3DL45Aj0f4EDPg4EHP78OULbTrUQ+moIyNN5A4FE+vQuTa5b2YCXkdTwsIDvvOtUiaANGM5bPBVf4s/7sEK1YHEeT19QV1lvWvWorBjDskRQMvNpWoAjc2YRoEDmtaCkfLn8D/x7pH4='}, {'kind': 'native', 'codec': 'bintools-v4', 'brep': '26411db2df8fa77051b37dfbed5925185810d476a1d9a555a0ca36ad058fdf45', 'faceColors': {}, 'contentHash': 'cbccb6aabd344d5ca5314286fa349e93ba29d7162354f7b55790f62395ac9f9d', 'payload': 'eJylVs1u00AQXju/ddPWtED5x3CCiAOkEUdwlSJxQCpSIu5W2oZKURwlBKmceA5eghdAKm/Ba1Q8AePEM5vYM9l1sap4tTM73zcz347rHY9PR0HnsNs5PHoX9OJxPIwHF8Gn9ovgWf95sLBG0350cup9iPvRl/N4NA1eep3Z5Otp62QatNqeo9jn6i2ubB3qoeB4KUTQkbIOv6/LQYQozsEE4YeCg7EOvuRQuA4iB+m5BgdbB/s6FC61OUL43xxyEeZ3ZBq8akk3xJ6dypD5nsUSyBRGoEdCkNouImQPGHOgAwYkEYEeCUGOmA1g6IP5QIaa9QEZ4WM8vBjEo4MjmMLp+njUm5xHo8FsSPO5O5ucRX1Q32uT+PIIdn5Xl7gqLKX1AIW7YB9IImS6PbkDRYELDwApkETIOJSFQAY/3eScwrxe93M0BoUdtL3a+5+/fvz98+0NHzVplKMc+GsaPBepzD2r6OnMN/i4rqMk/YHRhZe7jlFy+CnuPUnfIscwl80KRx5JhW5J5AjGMvyWBCNxfIxbSNbEUSmeI48EpaqIHMFYhVdZMOY5PkSOK8j8eShATUQGYx1+K4KRkBFQd7CySF45QbrjPEKfB7i4j85lRTwpuLNQ3D30yZSb+6bxAucuJNcYPkmo/Qa8qpLRUxYCv4N7+6Zswlw2Kxx5GtClTZEjGBvKQuC3cAvJmjgmD8exxgJBqbZEjmDcVhYCJ457yHEFuS7k6O6IyGD0lYXAEVB3kAR+O91xbqLPLi5uoLMWuKaRCtxnU9lg+fihm4Tk9QbGXTEVX6eCGei7uoLsScH31iEnufMSW0Kma0AzlIq4ne5QNdUWLgJ01kXUNNIiNthUNiW2iY54sYExYWAsIumBH7UN4by7vw45qY+xiHQHcNrrImLrHBIgyQhn7lIRNY20iHh9dEAc0FoUqGiFDVsKqIuWBkSt64D4DdD6pkuD/V4KqGuRBkTKzRIGvIseO+hJTcc5pAc7xmu6+N8MCqlJ379/iYwfsg==', 'glb': 'eNq1VktP20AQXt4QaEtbSnuq0J56MJG9fsW5FJq2EhUQBIgLymFjr6nVxI4ch4dQJP5Zr/1ZnbXXycYJIUVqJO/ON5755rE7Sa5a59/nEUKfSgh9XULox1n9+B7TbpcluHqPr1ncDaIQVzEpq1jBVyxkMU2iGDQu9QDivoLZbRLTLrcXOpA6NAYGXDFck3mu4du+axjEqdjMIcxQHd/SiWZ6mmk4quo41DXVpq/rnkqNZtO03YrGPOKQ3WsdwjajXuilAdoBsF+qZVURT0PBbXoLOgMwJKnooIOcfOqyWtSKYnC7BCMGidVavDCuwGEUMiD2GU16MZcSGkLmCUhdRtuweUwUy/IEaHwHInge0TDwo5YHqBf+CqObEDeyiGmOzZ7vs/giYDe4qivYBV9ohdXPkhgzMQYmGhE2J1EQJmOG5sCQGP0+mHZdyBBXVSHxwu4hPy+VoAsNZYCgc6z7M7WNo4Qm6aFe7qplW7U11bIrmlaxTMO2lGFvi68aPA5tsewAVE2R1jQYDyGideKgHSTBtYA0SeKg2Uuy6k/qZwfnB/XjNJ3j+unR/iEUDwUFoRekTSRABplDc/opM3VB3c0Oc7QpKm9KuwPHGSbndx1wMTViSZ2Co03V+OJbjd+lWW/QaBjt38IU3ckk9+Hd0K2B+1lt/3D/FPOihwRy2WnJzbuE1X0/nVIBD1l4lcAJk0qF3+b4ir/TDcciw2TGfFPjZ3ubcF1GvG0y6qxPcbaMQmgHyELaZoOvlnI2UVM4bMMY5dAcUiTJZm4KiaNbE3owTiKGcngy4lTk8ITwC7uzs/NnEaEvB8do8mdvLxdkZaZ/kHS5PNDvSfo9iadgX4wl88hxZc6xHCbwj/miR3gKsYo5yPwPvzmB4JlF/pwHnU2emTeXH54R46HgP01GhTqmynOI/zrPoQXYl9AiWoZ1Ga2gdbSK1mAtwf4KvUAbsG6gl+gt2kSvYX0D+3v0Dm3BuoW2gSH7LIt9Xsg5XoBnQ8JwfYFjiOGvAfogYb5/lPCcxDsn9nkJLwjOHC8K3xxz/lUJ83clCa+I/HLMbV9KeA2eTQmXRP45XodnS8Kca1vCT8zphPv82PzOPC/oibl4ZN6fmrv/8z3wF8Qy8dw=', 'selectors': 'eNqtWFuvozYQ/i88kyMwlyTndXtRVamVuqt9iaLKh0wStwQiQ+gerfLfOzNgrk7CKkcREDCe+b4ZzwV/d4rkCCf5FXSh8sx59V3nrPO9SsF5daQu1V4mpeM6kJX6/XeV7fDxGZ/jo0Se5ZtKVamgcF6/O7A7wKdUFoXaq0SWLO67I9NDrlV5POHEPFm8aTgvkjwrVXZR5fui8lGSzA6XVOoveQpaZgn8BAfnVbhOIU/nlIQHV7y5aMQCP6OWvyDbgVbZYayAICxSlUFRC65UoRjiOwMjURtnD7K8aMDhEhUjMfxXgDzhZQd4jxhKcLaoEnFmkJSw+wwpXhtGzPMoVUNPHbITCmHMwYt3bcX+gmhRYakvcMWnb2/5N5pxookb78Vzm2PrOieJQ5sQ7wUeKIW0F6Us2bB5kly0BjTMp/yCcMlJKcj9n5bnxVGee7dkseYudmvkzRiatwJdwrfmwepajw9s+4z9JtZnRUzISEAUV7bXWwoTpunllLE+tUPJZ1ke8ZLJEyku8otO4I/6BpcjgviN3ipx+RT7XBMWtndjkM9lvWR71qltYwY6O9VWMM87ixlzmZG+8bat5CHoNE9kysA6YvWt3qlMpvjvXwopO6+GQILkQFOUaJCkmJTAD+PfmpdmQmRCI7BNCH55P0OHp8WXod35tQb4LTtqDKaKwpxwp/JQ1D6Up6JbUr/q/HImzPXEJzDjC5VBnEJ24GXUYm6g3jLlbZc/oCF3/8ikyQFmQpf1KFLUEXZappQ2JpFCL3OC2XZKnzHBiO2NBXLtCyNVGyenJIpHdknT3mnjd9mrPX7g2dadnwZxAh4xXQWeVlsTbAbhS0Eg+URwKQfmKRmpwztf20Y0qP2XaOtGdCfoDYNg24RRq3xPevdGuYFDVTSVGS6NGCdvvL7QzYL+Lloc98B5I1OEbkhGwESp1cEyS35ThMyYnZTgPB+VMlZBWMUIqxhiDQdYB5JaqOFDO4YWqOEMqMJADQhqMIIatFB9UsXO8lqsLMafgL3ldK8Fu0KwUTzDrovemsZ5gQEbEthwBDa0gBXdIuiLGmAVdw2LQWAHKx6ADQ3YiMBGI7BRC3ZlsLbgakkLY955wdusvJjWwXKGafvZ4epGBmxMYOMR2NgONmCwd9NMcNOuwruDNLiNNN42RarNCEBIwZIRqC91SFYfZPQ4AXi9BCD4CJZuHN426U5p062O0LY99e7vo9SYI9c01nV0JlMAJQmwZIqag+gnteCRoYdJTDS/QNzgEEw4jFbyIxImhwClD7DkkJEjhM0R4m4mDl3Bh9UR4gMcYVILUFYBS2qxOGJ2NanNGCOI+J4jvGcdYVIOULYBS8rpOSK0RUQ4I3ULPiaOCD8oIkwmAkpCYMlEPUeEtogIH6Yen0KaTxNXhB8VE7GhsSQayxGN5cQVwuaKh5VJ1CerMz4iKpaGxYpYrEYsVlZnzG5cmopFYc2nm854Oi5WhsaaaKxHNNaGRthvcWa3NG3hFfUp9GeH97gXe0BjbWj4Hlc8b1zyPCuTYE5h7iLcp/Dm000mwbNMfK+lUhfvSfX2J1TEj/RunukxRH2yUhEf4RS/LeE+13B/XMR9YaUS2KjcaZe4iIt7VJ73Cn9t0fev6n1w1ft60/ZquBoe9SP9nsrU2opMVU2bnmHEzOnWms+DTjI1IdW0Exna6p71h+1H3Emm1qCa9gfDBTWnsakxrzvJVK+radEe1qRHVc7rl4dWNJXRalpLhxl2ThPQJLuoE02lrZrWt8HynRS1O7V51YmmelNNi86wus2pl83y91H0lveReLO83hk1O9z5f/ytgQsb7YYOxZKKxqcv1TVtRHDL4NNCoP9LHPBxIOLw588R2nhqhdBXQ0QrnbcQSKRP79LktpWNWE1ADQ8L+Mr7To0I2oLhBoWn4kv8gR83qJYszuPpS+oq222rEZU1Y1jVCGpmPk2LcCRkFhEKZF5LSsrX6/9zc6S1'}]


def seed_result(
    document: Path | str,
    descriptor: Mapping[str, Any] | None = None,
    *,
    model: Path | str | None = None,
    parts: bool = True,
    components: tuple[str, ...] = ("c0",),
    kind: str = "assembly-package",
    entry_kind: str = "part",
    sidecar: Mapping[str, Any] | None = None,
) -> str:
    """Seed geometry and, unless ``parts`` is False, each component's part as a build
    derives it under FIXTURE_PRODUCER (its display mesh, selector table and
    record), without native imports.

    Caller aliases are remapped to genuine geometry CIDs. Explicit malformed
    structure/geometry overrides stay malformed so completeness checks can reject them.
    """
    import base64
    import copy
    import zlib
    from cadgen._internal.component_package import canonical_json_bytes
    from cadgen.store.meshes import body_facts
    from cadgen.store.objects import put_object
    from cadgen.store.parts import _write, part_key
    from cadgen.store.records import note_document_tree, note_output, write_record
    from cadgen.store.trees import TREE_KIND, TREE_SCHEMA, tree_kind

    def inflate(text: str) -> bytes:
        return zlib.decompress(base64.b64decode(text))

    document = Path(document)
    descriptor = copy.deepcopy(dict(descriptor or {}))
    raw_components = descriptor.get("components")
    if not isinstance(raw_components, dict):
        raw_components = {cid: {} for cid in components}
    if len(raw_components) > len(_GEOMETRY_FIXTURES):
        raise ValueError("seed_result supports at most four independent fixture components")
    tree_components, aliases, component_colors = {}, {}, {}
    for index, (alias, raw_entry) in enumerate(raw_components.items()):
        fixture = _GEOMETRY_FIXTURES[index]
        entry = {name: copy.deepcopy(fixture[name]) for name in ("kind", "codec", "brep", "faceColors", "contentHash")}
        put_object(inflate(fixture["payload"]))
        if parts:
            key, glb = part_key(entry, FIXTURE_PRODUCER), inflate(fixture["glb"])
            if body_facts(glb)["part"] != key:
                raise ValueError("a seeded part's mesh names another part: derive the fixture parts again")
            _write(key, glb, inflate(fixture["selectors"]))
        raw_entry = dict(raw_entry or {})
        if raw_entry.get("color") is not None:
            entry["color"] = raw_entry["color"]
            component_colors[alias] = raw_entry["color"]
        if "brepObject" in raw_entry:
            entry["brep"] = raw_entry["brepObject"]
        cid = entry["contentHash"][:16]
        aliases[alias] = cid
        tree_components[cid] = entry
    identity = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
    occurrences = descriptor.get("occurrences")
    if not isinstance(occurrences, list):
        occurrences = [{"id": f"o1.{i}" if len(aliases) > 1 else "o1", "name": alias,
                        "component": cid, "transform": identity}
                       for i, (alias, cid) in enumerate(aliases.items(), 1)]
    else:
        for occurrence in occurrences:
            occurrence["component"] = aliases.get(occurrence.get("component"), occurrence.get("component"))
    children = [{"id": occurrence["id"], "name": occurrence.get("name", occurrence["id"]),
                 "nodeType": "part", "children": []} for occurrence in occurrences]
    assembly = descriptor.get("assembly")
    if not isinstance(assembly, dict):
        root = children[0] if len(children) == 1 and children[0]["id"] == "o1" else {
            "id": "o1", "name": descriptor.get("label") or "model", "nodeType": "assembly", "children": children}
        assembly = {"root": root}
    tree = {"kind": TREE_KIND, "schemaVersion": TREE_SCHEMA,
            "label": descriptor.get("label") or "model", "units": "mm",
            "components": tree_components, "occurrences": occurrences, "links": [],
            "assembly": assembly, "stats": {"occurrenceCount": len(occurrences), "linkCount": 0}}
    tree["entryKind"] = tree_kind(tree)
    if descriptor.get("kind") not in (None, kind):
        tree["kind"] = descriptor["kind"]
    for key in ("bbox", "capabilities", "edgeRendering", "color"):
        if key in descriptor:
            tree[key] = descriptor[key]
    # Deliberately malformed fixture knobs must remain malformed; production
    # publication goes through put_tree and native preparation instead.
    tree_hash = put_object(canonical_json_bytes(tree))
    try:
        sha = hashlib.sha256(document.read_bytes()).hexdigest()
    except OSError:
        sha = ""
    owner = Path(model) if model is not None else document
    record = {"entryKind": tree["entryKind"], "sourceKind": "python" if model is not None else "step",
              "tree": tree_hash, "closure": {"hash": sha, "files": [], "static": True}, "children": [],
              "outputs": {str(document.resolve()): {"sha256": sha}}, "stepHash": sha}
    if sidecar:
        record.update(sidecar)
    write_record(owner, record)
    if sha:
        note_document_tree(sha, tree_hash, kind="step", producer=FIXTURE_PRODUCER)
    if model is not None:
        note_output(document, owner)
    return tree_hash
