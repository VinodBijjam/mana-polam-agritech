import csv

with open("farm_data.csv", "r") as file:
    data = list(csv.DictReader(file))

farm = data[0]

print("Farmer ID:", farm["farmer_id"])
print("Field ID:", farm["field_id"])
print("Crop:", farm["crop"])
area = float(farm["area_acres"])

print("Area:", area, "acres")
print("Area in cents:", area * 100)
print("Water Source:", farm["water_source"])

question = input("Farmer question: ")

print("Farmer asked:", question)

crop_stage = input("Crop stage: ")
print("Crop stage:", crop_stage)

soil_moisture = float(input("Soil moisture (%): "))

print("Soil moisture:", soil_moisture, "%")

rainfall = float(input("Recent rainfall (mm): "))

print("Recent rainfall:", rainfall, "mm")

if soil_moisture < 30 and rainfall < 10:
    recommendation = "Consider irrigation"
else:
    recommendation = "Irrigation may not be needed immediately"

print("Recommendation:", recommendation)