#!/usr/bin/env python3
"""Allowlisted target/host selection for manual and scheduled builds."""

import json, sys

TARGETS = ["x86_64-unknown-linux-gnu", "x86_64-unknown-linux-musl"]
HOSTS = {
    "amazonlinux2": "public.ecr.aws/amazonlinux/amazonlinux:2",
    "alinux3": "alibaba-cloud-linux-3-registry.cn-hangzhou.cr.aliyuncs.com/alinux3/alinux3:latest",
    "centos7": "centos:7.9.2009",
    "ubuntu22": "ubuntu:22.04",
}


def selected(text, allowed):
    values = text.split(",")
    if (
        not values
        or len(values) != len(set(values))
        or any(v not in allowed for v in values)
    ):
        raise SystemExit("invalid or duplicate matrix selection: " + text)
    return values


if __name__ == "__main__":
    event, targets, hosts = sys.argv[1:]
    if event == "schedule":
        targets = ",".join(TARGETS)
        hosts = "all"
    t = selected(targets, TARGETS)
    h = list(HOSTS) if hosts == "all" else selected(hosts, HOSTS)
    print(
        json.dumps(
            {
                "include": [
                    {
                        "target": target,
                        "container": {"image": HOSTS[host], "label": host},
                    }
                    for target in t
                    for host in h
                ]
            },
            separators=(",", ":"),
        )
    )
