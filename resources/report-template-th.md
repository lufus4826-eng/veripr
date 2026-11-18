# แม่แบบรายงานผลกระทบ PR VeriPR (ภาษาไทย — Lazy-Load เฉพาะ Phase 4)

> **คำแนะนำ:** คัดลอกตั้งแต่ใต้เส้นแบ่งลงไปเขียนที่ `S/pr-report.md` ใส่ให้ครบทั้ง 7 หัวข้อ (ถ้าหัวข้อไหนว่างให้เขียน `"ไม่มี"`) **ห้ามลบหัวข้อทิ้ง** ใช้ข้อมูลจริงจาก `vpr.py summary --snippets` และไฟล์ `.patch` ใน `S/patches/` เท่านั้น

---

# รายงานตรวจสอบผลกระทบลูกโซ่และสัญญาข้อมูลก่อนส่งโค้ด (VeriPR Impact Report)

## ข้อมูลเซสชัน (Session Metadata)
- **รหัสเซสชัน (`S`):** `.bughunt/<SNAME>/`
- **โฟลเดอร์โปรเจกต์ (`ROOT`):** `<path>`
- **สาขา/สถานะที่เทียบ:** `Base: <base_ref>` เทียบกับ `Head: <head_ref>`
- **มาตรการตรวจ 2 ชั้น (Dual Gate):** ด่านที่ 1 Differential Worktree Check (`ab-verify`) | ด่านที่ 2 Zero-Ripple Fix + Feature Preservation (`verify-fix`)
- **สถานะความพร้อมส่งโค้ด:** <READY_TO_MERGE / BLOCKED_PENDING_FIX_APPLICATION>

---

## 1. บทสรุปผู้บริหารและตัวชี้วัด Dual-Gate

| ตัวชี้วัด | ผลลัพธ์ | หมายเหตุ |
|---|---:|---|
| ฟังก์ชัน/สัญลักษณ์ที่ถูกแก้ไข (`diff-scope`) | <N> | วิเคราะห์โครงสร้าง AST / สัญญาข้อมูลอัตโนมัติ |
| ไฟล์ผู้เรียกใช้ระยะ 1 ขั้น (`impact-map`) | <N> | จำกัดไม่เกิน 25 บรรทัด จัดอันดับจุดไร้เทสต์คุมก่อน |
| **จุดพังลูกโซ่ / สัญญา API เพี้ยนที่ยืนยันจริง** | **<N>** | ผ่านด่านที่ 1 (`GATE1_RIPPLE_CONFIRMED` / `GATE1_CONTRACT_DRIFT`) |
| **ชุดแก้ไขสะอาดที่ยืนยันผ่านและส่งออก Patch แล้ว** | **<N>** | ผ่านด่านที่ 2 `CONFIRMED_ZERO_RIPPLE_FIX` (ไม่พังของใหม่) |
| **เทสต์เก่าพังค้างที่คัดกรองออก (ไม่บล็อก PR)** | **<N>** | `PRE_EXISTING_FAILURE` (พังตั้งแต่โค้ดต้นฉบับ Base) |
| **แนวทางแก้ที่พังฟีเจอร์ใหม่และถูกย้อนกลับอัตโนมัติ** | **<N>** | ป้องกันด้วย `REJECTED_FEATURE_BROKEN` + `--auto-rollback` |

---

## 2. แผนผังการเปลี่ยนแปลงและผู้เรียกใช้ระยะ 1 ขั้น (`diff-scope` & `impact-map`)

| สัญลักษณ์ที่แก้ | ตำแหน่งโค้ด | ความเสี่ยงเชิงความหมาย | ผู้เรียกใช้ระยะ 1 ขั้น | มีเทสต์คุม? | ระดับความสำคัญ |
|---|---|---|---|---|---|
| `<symbol>` | `<file:line>` | `<risk_tags>` | `<caller (file:line)>` | `<YES/NO>` | `<HIGH/MED>` |

---

## 3. จุดพังลูกโซ่ / สัญญา API เพี้ยนที่ยืนยันจริงและชุดแก้ไขสะอาด (ด่าน 1 + ด่าน 2)

<!-- ทำซ้ำตามจำนวนข้อที่ CONFIRMED_ZERO_RIPPLE_FIX -->

### <ID>: <ชื่อสรุปปัญหา> (`<VERDICT>`)
- **จุดที่กระทบ / ผู้เรียกใช้:** [`<relative/path:line>`](<relative/path>) `<symbol>`
- **ต้นตอและกลไกผลกระทบลูกโซ่:** <อธิบายว่าทำไมการแก้ใน PR นี้จึงทำไฟล์ข้างเคียงหรือหน้าบ้านพังทั้งที่เทสต์เฉพาะจุดผ่าน>
- **สรุปผลตรวจ 2 ชั้น (Dual-Gate Proof):**
  - **ด่านที่ 1 (Differential Base vs. Head):** Base = `<PASS>` | Head = `<FAIL / CONTRACT_DRIFT>` (`schema_hash`: `<base_hash>` เทียบ `<head_hash>`)
  - **ด่านที่ 2 (Zero-Ripple Fix ใน `S/sandbox-fix/`):** จุดแก้ = `PASS` | ผู้เรียกใช้ 1 ขั้น = `PASS` | เทสต์รวมโปรเจกต์ = `<PASS / PASS_FILTERED_PREEXISTING>` | ฟีเจอร์ใหม่ใน PR ยังทำงาน = `PASS`
- **สคริปต์พิสูจน์ผล:** [`<S>/tests/<file>`](<S>/tests/<file>)
- **ไฟล์ Patch ส่วนเพิ่มที่ส่งออก:** [`<S>/patches/<id>.patch`](<S>/patches/<id>.patch)

#### หลักฐานย่อ (Base vs. Head vs. Fix)
```text
<คัดลอก snippet จาก vpr.py summary --snippets>
```

#### ชุดแก้ไขส่วนเพิ่มเทียบจากโค้ดปัจจุบัน (Unified Diff Patch)
```diff
<คัดลอก diff จาก S/patches/<id>.patch>
```

---

## 4. รายการเทสต์เก่าพังค้างที่คัดกรองออก (`PRE_EXISTING_FAILURE`) และแพตช์ที่ถูกย้อนกลับ

> VeriPR แยกแยะเทสต์ที่พังค้างบนสาขาหลักมาแต่เดิมไม่ให้บล็อกงานรอบนี้ และย้อนกลับแนวทางแก้ที่เผลอลบหรือทำฟีเจอร์ใหม่ของนักพัฒนาพังอัตโนมัติ

| ID | ผลลัพธ์ | สถานะ Base/Head หรือการย้อนกลับ | รายละเอียด / เหตุผล |
|---|---|---|---|
| <ID หรือ ไม่มี> | <PRE_EXISTING_FAILURE / REJECTED_FEATURE_BROKEN / REJECTED_CALLER_REGRESSION> | <Base=FAIL,Head=FAIL / AUTO_RESTORED> | <รายละเอียด> |

---

## 5. สรุปไฟล์ชุดแก้ไขส่วนเพิ่มที่ส่งออก (Exported Incremental Patches)
- **Patch แยกตามจุด:** `<S>/patches/<id>.patch`
- **Patch รวมสำหรับซ้อนทับงานปัจจุบัน:** `<S>/patches/veripr-fixes.patch`
- **คำสั่งนำไปใช้งาน:** `git apply <S>/patches/veripr-fixes.patch`

---

## 6. บันทึกความสะอาดและการเก็บกวาด Git Worktree
- **ไฟล์ Snapshot:** `<S>/logs/fs-before.txt`, `<S>/logs/fs-after.txt`
- **ผลการตรวจ:** `<cleanliness: CLEAN (0 new/modified files outside .bughunt/)>`
- **ไฟล์ส่งมอบที่เก็บไว้:** `<S>/pr-report.md`, `<S>/worklog.md`, `<S>/tests/`, `<S>/patches/`, `<S>/logs/`

---

## 7. บันทึกการนำ Patch ซ้อนทับโค้ดจริง (Applied Patches Log)
> อัปเดตเฉพาะเมื่อผู้ใช้อนุมัติให้รัน `git apply` ซ้อนทับงานจริงแล้วเท่านั้น

| วันเวลา | ไฟล์ Patch | ผลเทสต์บนโปรเจกต์จริง | สถานะ |
|---|---|---|---|
| ไม่มี | - | - | รอผู้ใช้อนุมัติ |
