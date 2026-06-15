# FlowAccount Connector (ERPNext ⇄ FlowAccount)

เชื่อม ERPNext กับ FlowAccount โดย ERPNext เป็นฐานข้อมูลหลักของบริษัท
และฝ่ายบัญชียังทำงานบนหน้า FlowAccount ได้ตามเดิม

ทิศทางข้อมูล (แต่ละประเภทวิ่งทางเดียว ป้องกันข้อมูลชนกัน):

| ข้อมูล | ทิศทาง | ปลายทางใน ERPNext |
|---|---|---|
| Quotation (ทีมขายสร้างใน ERPNext) | ERPNext → FlowAccount | — (push ตอน Submit) |
| Item ที่สร้างใหม่ใน ERPNext | ERPNext → FlowAccount | — (push เป็น product ชนิด non-inventory ตอนสร้าง) |
| ลูกค้า (Contacts ใน FlowAccount) | FlowAccount → ERPNext | Customer (upsert จริง) |
| สินค้า (Products ใน FlowAccount) | FlowAccount → ERPNext | Item (non-stock, upsert จริง) |

| เอกสารทุกชนิด: ใบเสนอราคา/ใบวางบิล/ใบแจ้งหนี้-ใบกำกับภาษี/ใบเสร็จ/ใบลดหนี้-เพิ่มหนี้/ค่าใช้จ่าย/ใบสั่งซื้อ | FlowAccount → ERPNext | FlowAccount Document (สำเนา read-only) |

> **สต๊อกมีเจ้าของเดียวคือ ERPNext** — สินค้าที่ push ไป FlowAccount เป็นชนิด non-inventory
> โดยเจตนา (FlowAccount ไม่มี API ปรับยอดสต๊อก และไม่ควรมีสต๊อกสองชุด)

> เอกสารขายจาก FlowAccount เก็บเป็น "สำเนา" ไม่สร้างเป็น Sales Invoice จริง
> เพื่อไม่ให้ ERPNext ลงบัญชี GL ซ้ำกับสมุดบัญชีของ FlowAccount

## ตัดสต๊อกจากยอดขาย B2B ฝั่ง FlowAccount (stock_out.py)

เอกสารขายที่ออกใน FlowAccount (เช่นใบกำกับภาษี) ถูก pull เข้ามา แล้วตัดสต๊อกใน ERPNext
เป็น **Stock Entry (Material Issue)** — ตัดของออกโดยไม่ลงรายได้/COGS ซ้ำ (บัญชีอยู่ FlowAccount)

| Site Config key | ค่า |
|---|---|
| `flowaccount_deduct_stock` | `1` เพื่อเปิด (ปิดไว้ default) |
| `flowaccount_deduct_doctypes` | (ไม่บังคับ) ชนิดเอกสารที่ตัด คั่น comma · default `tax-invoice` |
| `flowaccount_stock_warehouse` | fallback ถ้าไม่ได้ตั้ง KGF Stock Settings.default_warehouse |

- คลังดึงจาก **KGF Stock Settings** (แหล่งเดียวกับ kgf_stock) ก่อน แล้วค่อย fallback
- ตัด **เฉพาะ stock item** ที่ map ได้มั่นใจ (product id → `flowaccount_product_id` หรือรหัส → `item_code`)
  · บรรทัดที่ไม่พบ/ไม่ใช่ stock item → ข้าม + log (ไม่เดาตัดผิด)
- **idempotent**: แต่ละเอกสารตัดได้ครั้งเดียว (ฟิลด์ `stock_deducted` + `stock_entry` บน mirror)
- ทำงานตอน mirror ใหม่ถูกสร้างเท่านั้น (ไม่ตัดซ้ำตอน re-pull)

> ⚠️ ก่อนเปิด: ยืนยัน item matching กับเอกสารจริง 1 รอบ · เปิด `is_stock_item=1` + ใส่ยอดตั้งต้น
> เฉพาะ SKU ที่คุมสต๊อก · เอกสารเก่าที่ pull ก่อนเปิดฟีเจอร์จะไม่ถูกตัดย้อนหลังอัตโนมัติ

ติดตั้งบน Frappe Cloud ได้ **โดยไม่ต้องมี dev environment / ไม่ต้องแตะ terminal**

---

## ขั้นตอนแบบผ่านเว็บล้วน (ไม่มี terminal)

### ขั้นที่ 1 — เอาโค้ดขึ้น GitHub
1. สมัคร/ล็อกอิน GitHub (ฟรี)
2. กด **New repository** ตั้งชื่อ `flowaccount_connector` เลือก Public แล้ว Create
3. ในหน้า repo กด **Add file → Upload files** แล้วลาก **เนื้อหาทั้งหมดในโฟลเดอร์นี้**
   (ตัวไฟล์ `pyproject.toml`, `license.txt`, และโฟลเดอร์ `flowaccount_connector/`)
   เข้าไปวาง แล้ว Commit
   > ต้องให้ `pyproject.toml` อยู่ที่ "ราก" ของ repo

### ขั้นที่ 2 — เพิ่ม app เข้า Bench บน Frappe Cloud
1. dashboard → **Benches** → เลือก bench ที่ site ของคุณใช้อยู่
2. แท็บ **Apps → Add App** → เลือกแบบ **From GitHub / Public URL**
3. วาง URL repo ของคุณ + branch `main` → Add
4. กด **Deploy / Update** รอบิลด์เสร็จ
   > ถ้าหน้านี้ให้เลือกได้แค่ app จาก marketplace ไม่มีช่องใส่ GitHub URL
   > แปลว่าแพลน/bench เป็นแบบ shared ที่เพิ่ม custom app ไม่ได้ —
   > กรณีนี้ให้บอกผม จะเปลี่ยนไปวิธี Server Script (วางโค้ดในเว็บแทน)

### ขั้นที่ 3 — Install ลง site
dashboard → **Sites → kgarden.s.frappe.cloud → Apps → Install App**
→ เลือก `flowaccount_connector`
(custom field 2 ตัวบน Quotation จะถูกสร้างให้อัตโนมัติ ไม่ต้องไปเพิ่มเอง)

### ขั้นที่ 4 — ใส่ credential ผ่าน Site Config
dashboard → **Sites → ... → Site Config** เพิ่ม key เหล่านี้:

| Key                         | Value                                    |
|-----------------------------|------------------------------------------|
| `flowaccount_enabled`       | `1`                                      |
| `flowaccount_client_id`     | (client id ของคุณ)                       |
| `flowaccount_client_secret` | (client secret ตัวใหม่ที่ regenerate แล้ว) |
| `flowaccount_pull_enabled`  | `1` เมื่อพร้อมเปิด pull sync (ปิดไว้โดย default) |
| `flowaccount_pull_pages`    | (ไม่บังคับ) จำนวนหน้าที่ดึงต่อรอบ ค่าเริ่มต้น 5 หน้า × 50 รายการ |
| `flowaccount_push_disabled` | `1` บน site UAT/ทดสอบ — ปิดขา push (กันเอกสารทดสอบหลุดเข้า FlowAccount จริง) แต่ pull ยังทำงาน |
| `flowaccount_extra_accounts` | (ไม่บังคับ) รายชื่อบัญชีเพิ่ม คั่นด้วย comma เช่น `shop` สำหรับหน้าร้าน B2C/no-VAT |
| `flowaccount_shop_client_id` / `flowaccount_shop_client_secret` | credentials ของบัญชีหน้าร้าน (ใช้ชื่อ key ตาม label ใน extra_accounts) |

## โมเดล B2B / B2C (ตัดสินใจ มิ.ย. 2026)

- **B2B (มี VAT)**: เอกสารทางการอยู่ที่ FlowAccount บัญชีบริษัท — Quotation จาก ERPNext push ออกอัตโนมัติ
- **B2C / หน้าร้าน (no-VAT)**: ออกเอกสารใน **ERPNext เท่านั้น** (Quotation → Sales Invoice ปกติ)
  ไม่ push ออก — ใช้ Customer/Item ชุดเดียวกับที่ sync มาจาก FlowAccount
- ฟิลด์ "FlowAccount Entity" บน Quotation คุมปลายทาง:
  - `Auto` (ค่าเริ่มต้น): มี VAT → push เข้าบัญชีบริษัท / ไม่มี VAT → เก็บใน ERPNext ไม่ส่งออก
  - `Company` / `Shop` / `ERPNext Only`: บังคับเอง
- ขา pull รองรับหลายบัญชี FlowAccount อยู่แล้ว (`flowaccount_extra_accounts`) เผื่อวันหน้า
  หน้าร้านเปิดบัญชี FlowAccount ของตัวเอง — เอกสารมีฟิลด์ Account แยก, ID มี prefix กันชนกัน

> ใช้ secret **ตัวใหม่** ที่ regenerate หลังจากที่ตัวเดิมเคยถูกแชร์ออกไป

### ขั้นที่ 5 — ทดสอบ
สร้างใบเสนอราคาทดสอบ 1 ใบ แล้ว Submit → ดูว่า field "FlowAccount Document"
มีเลขเด้งขึ้น และเอกสารโผล่ใน FlowAccount (สถานะ awaiting/รออนุมัติ)
ถ้าพลาด ดูที่ **Error Log** ใน ERPNext — มีข้อความจาก FlowAccount + payload ที่ส่งไป

---

## VAT / no-VAT
ถ้าใบเสนอราคามีบรรทัดภาษี → ส่ง `isVat: true` (มี 7%)
ถ้าไม่มี → `isVat: false` (no-VAT) อัตโนมัติ
(ใช้ร่วมกับ Tax Template ของ ERPNext Thailand ได้เลย)

## โครงสร้างไฟล์
```
pyproject.toml                       # ต้องอยู่ที่ราก repo
license.txt
flowaccount_connector/
  hooks.py                           # ผูก event on_submit ของ Quotation
  modules.txt  patches.txt
  fixtures/custom_field.json         # สร้าง custom field อัตโนมัติตอน install
  flowaccount_connector/doctype/
    flowaccount_document/            # DocType สำเนาเอกสารจาก FlowAccount
  flowaccount/
    client.py                        # token (cache) + REST helper + pagination
    mapping.py                       # Quotation -> payload FlowAccount
    events.py                        # background job ตอน submit (push)
    sync.py                          # scheduled job รายชั่วโมง (pull)
```

## Pull sync ทำงานยังไง
- รันอัตโนมัติทุกชั่วโมง (scheduler) — ไม่ทำอะไรเลยจนกว่าจะตั้ง `flowaccount_pull_enabled = 1`
- ลูกค้า: จับคู่ด้วย FlowAccount ID → เลขผู้เสียภาษี → ชื่อ ก่อนสร้างใหม่ (กันข้อมูลซ้ำ)
- สินค้า: สร้างเป็น non-stock item เสมอ จะไม่ไปแตะตัวเลขคลังสินค้า
- เอกสาร: ดูได้ที่ list "FlowAccount Document" ใน ERPNext (มี payload JSON เต็มแนบไว้)
- backfill ข้อมูลเก่าทั้งหมด: ตั้ง `flowaccount_pull_pages` สูง ๆ ชั่วคราว (เช่น 100) หนึ่งรอบ แล้วลดกลับ

## ปรับแต่งภายหลัง (แก้ใน mapping.py)
- `creditType` ตอนนี้ = 3 (เงินสด) ถ้าให้เครดิตเปลี่ยนเป็น 1 + ใส่ `creditDays`
- `_item_type()` map จาก is_stock_item — ปรับให้ตรงสินค้าจริงได้
- ต่อยอด sync ใบกำกับ/ใบเสร็จ ใช้ pattern เดียวกัน เปลี่ยน endpoint + payload
