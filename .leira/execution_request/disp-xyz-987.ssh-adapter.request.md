# Execution Request

## Dispatch Plan

```text
# Dispatch Plan

## Dispatch ID

disp-xyz-987

## Subject

subj-123

## Subject Kind

codebase

## Dispatch Type

deployment

## Target

ssh-adapter

## Execution Mode

interactive

## Reason Codes

* manual_trigger
* prod_deploy

## Dispatch Summary

Deploying package updates.

```

## Execution Capability

* Adapter Label: ssh-adapter
* Adapter Kind: ssh
* Supported Dispatch Types:
  * deployment
* Supported Subject Kinds:
  * codebase
* Supported Execution Modes:
  * interactive
* Supports Parallel Execution: False
* Supports Dry Run: True
* Supports Interactive Execution: True

## Execution Request

* Dispatch ID: disp-xyz-987
* Adapter Label: ssh-adapter
* Request Summary: Execution request for dispatch plan 'disp-xyz-987' using compatible adapter 'ssh-adapter'.

## Provenance Notice

> This request describes exactly what an execution adapter would receive. It performs no execution, scheduling, planning, orchestration, approval, or dispatch.
