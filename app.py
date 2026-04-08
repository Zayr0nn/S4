from flask import Flask, render_template, request, redirect

app = Flask(__name__)

barracas = {}

@app.route("/")
def index():
    return render_template("index.html", barracas=barracas)

@app.route("/cadastro", methods=["GET", "POST"])
def cadastro():
    if request.method == "POST":
        nome = request.form["nome"]
        responsavel = request.form["responsavel"]
        pix = request.form["pix"]

        barracas[nome] = {
            "responsavel": responsavel,
            "pix": pix,
            "total": 0,
            "vendas": []
        }

        return redirect("/")

    return render_template("cadastro.html")

@app.route("/comprar/<nome>", methods=["GET", "POST"])
def comprar(nome):
    barraca = barracas[nome]

    if request.method == "POST":
        valor = float(request.form["valor"])

        barraca["total"] += valor
        barraca["vendas"].append(valor)

        return redirect("/")

    return render_template("comprar.html", nome=nome, barraca=barraca)

@app.route("/relatorio")
def relatorio():
    total_geral = sum(b["total"] for b in barracas.values())
    return render_template("relatorio.html", barracas=barracas, total_geral=total_geral)

if __name__ == "__main__":
    app.run()